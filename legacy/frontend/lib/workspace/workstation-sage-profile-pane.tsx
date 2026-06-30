'use client';

import { useEffect, useMemo, useState } from 'react';

import { FormField, FormGrid, FormTextarea, FormInput } from '@/lib/ui/form-controls';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import type {
  WorkstationSageProfileQuestionRecord,
  WorkstationSageProfileRecord,
} from '@/lib/workspace/workstation-client';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';
import {
  WorkstationActionButton,
  WorkstationSurfaceCard,
  WorkstationSurfaceList,
  WorkstationSurfaceListItem,
  WorkstationSurfaceNotice,
  WorkstationSurfaceRoot,
  WorkstationSurfaceStat,
  WorkstationSurfaceStatGrid,
} from '@/lib/workspace/workstation-surface-primitives';

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

type BootstrapField = keyof SageProfileDraft;

const BOOTSTRAP_FIELDS: BootstrapField[] = [
  'user_name',
  'identity_summary',
  'communication_style',
  'recurring_responsibility',
  'standing_rules_text',
];

const BOOTSTRAP_FIELD_LABELS: Record<BootstrapField, string> = {
  user_name: 'What Sage should call you',
  identity_summary: 'Role and active work',
  communication_style: 'Communication style',
  recurring_responsibility: 'Recurring responsibility',
  standing_rules_text: 'Standing rules',
};

const BOOTSTRAP_FIELD_PLACEHOLDERS: Record<BootstrapField, string> = {
  user_name: 'Example: Mansur',
  identity_summary: 'Example: I run product and engineering for a mobile-first agent platform.',
  communication_style: 'Example: Be direct, concise, and lead with the answer.',
  recurring_responsibility: 'Example: Keep my inbox triaged and surface urgent replies.',
  standing_rules_text: 'Example: Never send external messages without approval.',
};

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
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
      answered_count: readNumber(bootstrapRecord.answered_count),
      total_count: Math.max(1, readNumber(bootstrapRecord.total_count, 5)),
      progress_label: readString(bootstrapRecord.progress_label) || `${readNumber(bootstrapRecord.answered_count)}/${Math.max(1, readNumber(bootstrapRecord.total_count, 5))}`,
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

function draftFromProfileSnapshot(snapshot: SageProfileSnapshot): SageProfileDraft {
  return {
    user_name: snapshot.profile.user_name,
    identity_summary: snapshot.profile.identity_summary,
    communication_style: snapshot.profile.communication_style,
    recurring_responsibility: snapshot.profile.recurring_responsibility,
    standing_rules_text: snapshot.profile.standing_rules_text,
  };
}

function normalizeBootstrapField(field: unknown): BootstrapField | null {
  const fieldName = readString(field);
  if (fieldName === 'standing_rules') {
    return 'standing_rules_text';
  }
  return BOOTSTRAP_FIELDS.includes(fieldName as BootstrapField) ? fieldName as BootstrapField : null;
}

export function WorkstationSageProfilePane() {
  const services = useWorkspaceServices();
  const cachedPayload = services.queryClient.peek<unknown>('chat:canonical:sage-profile');
  const cached = cachedPayload ? normalizeProfileSnapshot(cachedPayload) : null;
  const [snapshot, setSnapshot] = useState<SageProfileSnapshot>(() => cached ?? defaultProfileSnapshot());
  const [draft, setDraft] = useState<SageProfileDraft>(() => draftFromProfileSnapshot(cached ?? defaultProfileSnapshot()));
  const [isLoading, setIsLoading] = useState(() => cached === null);
  const [isSaving, setIsSaving] = useState(false);
  const [isBootstrapSaving, setIsBootstrapSaving] = useState(false);
  const [activeBootstrapField, setActiveBootstrapField] = useState<BootstrapField | null>(() =>
    normalizeBootstrapField((cached ?? defaultProfileSnapshot()).bootstrap.current_question?.field) ?? 'user_name',
  );
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const refresh = async (showLoading = false) => {
    if (showLoading) {
      setIsLoading(true);
    }
    setError(null);
    const payload = await services.client.getSageProfile();
    const nextSnapshot = normalizeProfileSnapshot(payload);
    services.queryClient.set('chat:canonical:sage-profile', nextSnapshot);
    setSnapshot(nextSnapshot);
    setDraft(draftFromProfileSnapshot(nextSnapshot));
    setIsLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    void refresh(cached === null).catch((loadError) => {
      if (!cancelled) {
        setError(loadError instanceof Error ? loadError.message : 'Profile is unavailable right now.');
        setIsLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [services.client]);

  const saveDraft = async () => {
    if (isSaving) {
      return;
    }
    setIsSaving(true);
    setError(null);
    setStatus(null);
    try {
      const payload = await services.client.updateSageProfile({
        userName: draft.user_name,
        identitySummary: draft.identity_summary,
        communicationStyle: draft.communication_style,
        recurringResponsibility: draft.recurring_responsibility,
        standingRulesText: draft.standing_rules_text,
      });
      const nextSnapshot = normalizeProfileSnapshot(payload);
      services.queryClient.set('chat:canonical:sage-profile', nextSnapshot);
      setSnapshot(nextSnapshot);
      setDraft(draftFromProfileSnapshot(nextSnapshot));
      setStatus(nextSnapshot.bootstrap.complete ? 'Profile updated.' : 'Bootstrap progress saved.');
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Could not update the profile.');
    } finally {
      setIsSaving(false);
    }
  };

  const saveSnapshot = (payload: WorkstationSageProfileRecord) => {
    const nextSnapshot = normalizeProfileSnapshot(payload);
    services.queryClient.set('chat:canonical:sage-profile', nextSnapshot);
    setSnapshot(nextSnapshot);
    setDraft(draftFromProfileSnapshot(nextSnapshot));
    return nextSnapshot;
  };

  const saveBootstrapDraft = async () => {
    if (isBootstrapSaving || snapshot.bootstrap.complete) {
      return;
    }
    const field = activeBootstrapField ?? normalizeBootstrapField(snapshot.bootstrap.current_question?.field);
    if (!field) {
      return;
    }
    const answer = draft[field].trim();
    if (!answer) {
      setError('Answer this question before continuing.');
      return;
    }
    setIsBootstrapSaving(true);
    setError(null);
    setStatus(null);
    try {
      const currentField = normalizeBootstrapField(snapshot.bootstrap.current_question?.field);
      const payload = field === currentField
        ? await services.client.answerSageProfileBootstrap({ answer })
        : await services.client.updateSageProfile({
          userName: draft.user_name,
          identitySummary: draft.identity_summary,
          communicationStyle: draft.communication_style,
          recurringResponsibility: draft.recurring_responsibility,
          standingRulesText: draft.standing_rules_text,
        });
      const nextSnapshot = saveSnapshot(payload);
      const nextField = normalizeBootstrapField(nextSnapshot.bootstrap.current_question?.field);
      setActiveBootstrapField(nextField);
      setStatus(nextSnapshot.bootstrap.complete ? 'Sage knows you now.' : null);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Could not save this answer.');
    } finally {
      setIsBootstrapSaving(false);
    }
  };

  const projectionItems = useMemo(
    () => Object.entries(snapshot.projections).map(([filename, content]) => ({
      filename,
      preview: content.split('\n').find((line) => readString(line)) || 'Generated projection',
    })),
    [snapshot.projections],
  );
  const profileAuthority = readString(snapshot.storagePolicy.authority).replace(/_/g, ' ') || 'structured profile cloud canonical';
  const projectedFiles = Array.isArray(snapshot.storagePolicy.projected_files)
    ? snapshot.storagePolicy.projected_files.flatMap((value) => {
      const token = readString(value);
      return token ? [token] : [];
    })
    : [];
  const bootstrapQuestion = snapshot.bootstrap.current_question;
  const displayNameHint = snapshot.accountSeed.display_name || snapshot.accountSeed.email || 'Set a preferred name';
  const currentBootstrapField = normalizeBootstrapField(bootstrapQuestion?.field);
  const activeField = activeBootstrapField ?? currentBootstrapField ?? 'user_name';
  const activeFieldIndex = Math.max(0, BOOTSTRAP_FIELDS.indexOf(activeField));
  const activeFieldIsCurrent = activeField === currentBootstrapField;
  const canGoBack = activeFieldIndex > 0;
  const canGoForward = activeFieldIndex < Math.min(snapshot.bootstrap.answered_count, BOOTSTRAP_FIELDS.length - 1);
  const forwardLabel = activeFieldIndex + 1 === snapshot.bootstrap.answered_count ? 'Current' : 'Next';
  const activeQuestionLabel = activeFieldIsCurrent && bootstrapQuestion?.prompt
    ? readString(bootstrapQuestion.prompt)
    : BOOTSTRAP_FIELD_LABELS[activeField];
  const activePlaceholder = activeFieldIsCurrent && bootstrapQuestion?.placeholder
    ? readString(bootstrapQuestion.placeholder)
    : BOOTSTRAP_FIELD_PLACEHOLDERS[activeField];

  useEffect(() => {
    if (snapshot.bootstrap.complete) {
      setActiveBootstrapField(null);
      return;
    }
    const nextField = normalizeBootstrapField(snapshot.bootstrap.current_question?.field);
    if (!nextField) {
      return;
    }
    setActiveBootstrapField((current) => {
      if (!current) {
        return nextField;
      }
      const currentIndex = BOOTSTRAP_FIELDS.indexOf(current);
      const nextIndex = BOOTSTRAP_FIELDS.indexOf(nextField);
      return currentIndex >= 0 && currentIndex <= nextIndex ? current : nextField;
    });
  }, [snapshot.bootstrap.complete, snapshot.bootstrap.current_question?.field]);

  return (
    <WorkstationSurfaceRoot surface="sage-profile">
      <main className="app-stack-4">
        {status ? <WorkstationSurfaceNotice tone="success">{status}</WorkstationSurfaceNotice> : null}
        {error ? <WorkstationSurfaceNotice tone="warning">{error}</WorkstationSurfaceNotice> : null}

        {isLoading ? (
          <div className="app-stack-3">
            <SkeletonBlock height="7rem" />
            <SkeletonBlock height="18rem" />
            <SkeletonBlock height="10rem" />
          </div>
        ) : (
          <>
            <WorkstationSurfaceStatGrid>
              <WorkstationSurfaceStat
                label="Bootstrap"
                value={snapshot.bootstrap.complete ? 'Complete' : 'In progress'}
                hint={snapshot.bootstrap.progress_label}
              />
              <WorkstationSurfaceStat
                label="Profile authority"
                value={profileAuthority}
                hint="Structured profile drives USER, IDENTITY, SOUL, and HEARTBEAT."
              />
              <WorkstationSurfaceStat
                label="Projected files"
                value={projectedFiles.length || 4}
                hint={projectedFiles.join(' · ') || 'USER.md · IDENTITY.md · SOUL.md · HEARTBEAT.md'}
              />
            </WorkstationSurfaceStatGrid>

            {!snapshot.bootstrap.complete ? (
              <WorkstationSurfaceCard
                title="Meet Sage"
                description="Answer one question at a time. Sage uses the existing profile bootstrap route to build durable context."
                actions={(
                  <WorkstationActionButton
                    type="button"
                    tone="primary"
                    disabled={isBootstrapSaving}
                    onClick={() => {
                      void saveBootstrapDraft();
                    }}
                  >
                    {isBootstrapSaving ? 'Saving…' : activeFieldIsCurrent ? 'Save answer' : 'Update answer'}
                  </WorkstationActionButton>
                )}
              >
                <div className="app-stack-3">
                  <WorkstationSurfaceNotice tone="warning">
                    Question {activeFieldIndex + 1} of {snapshot.bootstrap.total_count}
                  </WorkstationSurfaceNotice>
                  <FormField label={activeQuestionLabel}>
                    {activeField === 'user_name' || activeField === 'recurring_responsibility' ? (
                      <FormInput
                        value={draft[activeField]}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          setDraft((current) => ({ ...current, [activeField]: value }));
                        }}
                        placeholder={activeField === 'user_name' ? displayNameHint : activePlaceholder}
                      />
                    ) : (
                      <FormTextarea
                        rows={activeField === 'standing_rules_text' ? 5 : 4}
                        value={draft[activeField]}
                        onChange={(event) => {
                          const value = event.currentTarget.value;
                          setDraft((current) => ({ ...current, [activeField]: value }));
                        }}
                        placeholder={activePlaceholder}
                      />
                    )}
                  </FormField>
                  <div className="app-cluster">
                    <WorkstationActionButton
                      type="button"
                      tone="secondary"
                      disabled={!canGoBack || isBootstrapSaving}
                      onClick={() => {
                        setActiveBootstrapField(BOOTSTRAP_FIELDS[activeFieldIndex - 1] ?? activeField);
                      }}
                    >
                      Back
                    </WorkstationActionButton>
                    <WorkstationActionButton
                      type="button"
                      tone="secondary"
                      disabled={!canGoForward || isBootstrapSaving}
                      onClick={() => {
                        setActiveBootstrapField(BOOTSTRAP_FIELDS[activeFieldIndex + 1] ?? activeField);
                      }}
                    >
                      {forwardLabel}
                    </WorkstationActionButton>
                  </div>
                </div>
              </WorkstationSurfaceCard>
            ) : (
              <WorkstationSurfaceNotice tone="success">Sage knows you now.</WorkstationSurfaceNotice>
            )}

            <WorkstationSurfaceCard
              title="Profile"
              description="USER, IDENTITY, and SOUL stay structured here. Sage uses this state directly on normal turns."
              actions={(
                <WorkstationActionButton
                  type="button"
                  tone="primary"
                  disabled={isSaving}
                  onClick={() => {
                    void saveDraft();
                  }}
                >
                  {isSaving ? 'Saving…' : 'Save profile'}
                </WorkstationActionButton>
              )}
            >
              <div className="app-stack-3">
                <WorkstationSurfaceNotice tone={snapshot.bootstrap.complete ? 'success' : 'warning'}>
                  {snapshot.bootstrap.complete
                    ? 'Bootstrap complete.'
                    : `Bootstrap in progress · ${snapshot.bootstrap.progress_label}${bootstrapQuestion?.prompt ? ` · Next: ${readString(bootstrapQuestion.prompt)}` : ''}`}
                </WorkstationSurfaceNotice>
                <FormGrid columns="repeat(2, minmax(0, 1fr))">
                  <FormField label="What Sage should call you">
                    <FormInput
                      value={draft.user_name}
                      onChange={(event) => {
                        setDraft((current) => ({ ...current, user_name: event.currentTarget.value }));
                      }}
                      placeholder={displayNameHint}
                    />
                  </FormField>
                  <FormField label="Recurring responsibility">
                    <FormInput
                      value={draft.recurring_responsibility}
                      onChange={(event) => {
                        setDraft((current) => ({ ...current, recurring_responsibility: event.currentTarget.value }));
                      }}
                      placeholder="Example: Keep my inbox triaged."
                    />
                  </FormField>
                </FormGrid>
                <FormGrid columns="1fr">
                  <FormField label="Role and active work">
                    <FormTextarea
                      rows={4}
                      value={draft.identity_summary}
                      onChange={(event) => {
                        setDraft((current) => ({ ...current, identity_summary: event.currentTarget.value }));
                      }}
                      placeholder="Example: I run product and engineering for Empyralis."
                    />
                  </FormField>
                  <FormField label="Communication style">
                    <FormTextarea
                      rows={4}
                      value={draft.communication_style}
                      onChange={(event) => {
                        setDraft((current) => ({ ...current, communication_style: event.currentTarget.value }));
                      }}
                      placeholder="Example: Be direct, concise, and lead with the answer."
                    />
                  </FormField>
                  <FormField label="Standing rules" hint="One rule per line. This becomes Sage's durable personal operating style, not freeform chat memory.">
                    <FormTextarea
                      rows={5}
                      value={draft.standing_rules_text}
                      onChange={(event) => {
                        setDraft((current) => ({ ...current, standing_rules_text: event.currentTarget.value }));
                      }}
                      placeholder="Example: Never send external messages without approval."
                    />
                  </FormField>
                </FormGrid>
              </div>
            </WorkstationSurfaceCard>

            <WorkstationSurfaceCard
              title="Projected context files"
              description="These files remain visible projections for advanced inspection and export. The structured profile stays authoritative."
            >
              <WorkstationSurfaceList>
                {projectionItems.map((item) => (
                  <WorkstationSurfaceListItem
                    key={item.filename}
                    title={item.filename}
                    description={item.preview}
                  />
                ))}
              </WorkstationSurfaceList>
            </WorkstationSurfaceCard>
          </>
        )}
      </main>
    </WorkstationSurfaceRoot>
  );
}
