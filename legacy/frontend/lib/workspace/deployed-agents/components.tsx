'use client';

import { useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';

import {
  DataBadge,
} from '@/lib/ui/data-table';
import {
  FormGrid,
  FormReadout,
} from '@/lib/ui/form-controls';
import { ListDetailPanel } from '@/lib/ui/list-detail';
import { AppSurfaceStatGrid, joinClassNames } from '@/lib/ui/primitives';
import { SkeletonBlock } from '@/lib/ui/skeleton-block';
import { WorkstationSplitWorkbench } from '@/lib/workspace/workstation-split-workbench';

import type {
  LaunchReadinessItem,
  StudioTemplate,
  TimelineEntry,
  WizardState,
} from './types';
import {
  CONTEXT_PRESET_OPTIONS,
  RETENTION_PRESET_OPTIONS,
  STUDIO_DEFAULT_RUNTIME_PLACEMENT,
  STUDIO_RUNTIME_OPTIONS,
  normalizeContextPresetId,
} from './constants';
import {
  formatTimestamp,
  humanizeToken,
  normalizeRuntimePlacement,
  readOptionalString,
  resolveRuntimeCardStatus,
  studioRuntimeOption,
  transcriptEntryBody,
  transcriptEntryTitle,
  transcriptEntryTone,
} from './utils';

export function ContextPresetControl({
  value,
  onSelect,
}: {
  value: string;
  onSelect: (nextValue: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [menuPlacement, setMenuPlacement] = useState<'down' | 'up'>('down');
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const normalizedValue = normalizeContextPresetId(value);
  const selectedOption = CONTEXT_PRESET_OPTIONS.find((option) => option.id === normalizedValue) ?? CONTEXT_PRESET_OPTIONS[1] ?? CONTEXT_PRESET_OPTIONS[0];
  const resolveMenuPlacement = () => {
    const button = buttonRef.current;
    if (!button) {
      return 'down';
    }
    const buttonRect = button.getBoundingClientRect();
    const menuHeight = CONTEXT_PRESET_OPTIONS.length * 38 + 10;
    const spaceBelow = window.innerHeight - buttonRect.bottom;
    const spaceAbove = buttonRect.top;
    return spaceBelow < menuHeight && spaceAbove > spaceBelow ? 'up' : 'down';
  };
  const openMenu = () => {
    setMenuPlacement(resolveMenuPlacement());
    setIsOpen(true);
  };
  const toggleMenu = () => {
    if (isOpen) {
      setIsOpen(false);
      return;
    }
    openMenu();
  };

  return (
    <div className="deployed-agents-context-select">
      <div className="deployed-agents-context-select__copy">
        <div className="deployed-agents-context-select__label">Knowledge depth</div>
        <p>Controls how much file and source context this Business Agent can use when answering.</p>
        {normalizedValue === 'max' ? (
          <p className="deployed-agents-context-select__warning">Max uses the largest safe context for the selected model and can cost more.</p>
        ) : null}
      </div>
      <div className="deployed-agents-context-select__control">
        <button
          ref={buttonRef}
          type="button"
          className="deployed-agents-context-select__button"
          aria-haspopup="menu"
          aria-expanded={isOpen}
          onClick={toggleMenu}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              setIsOpen(false);
            }
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              openMenu();
            }
          }}
        >
          <span>{selectedOption.label}</span>
          <ChevronDown aria-hidden="true" size={18} />
        </button>
        {isOpen ? (
          <div
            className={joinClassNames(
              'deployed-agents-context-select__menu',
              menuPlacement === 'up' && 'deployed-agents-context-select__menu--up',
            )}
            role="menu"
            aria-label="Knowledge depth"
          >
            {CONTEXT_PRESET_OPTIONS.map((option) => {
              const selected = normalizedValue === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  className={joinClassNames(
                    'deployed-agents-context-select__option',
                    selected && 'deployed-agents-context-select__option--selected',
                  )}
                  role="menuitemradio"
                  aria-checked={selected}
                  onClick={() => {
                    onSelect(option.id);
                    setIsOpen(false);
                  }}
                >
                  <span>{option.label}</span>
                  {selected ? <Check aria-hidden="true" size={18} /> : null}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function RetentionPresetControl({
  value,
  onSelect,
}: {
  value: string;
  onSelect: (nextValue: string) => void;
}) {
  return (
    <div className="deployed-agents-context-presets">
      <div className="deployed-agents-context-presets__label">Customer memory length</div>
      <div className="deployed-agents-context-presets__grid" role="tablist" aria-label="Customer memory length presets">
        {RETENTION_PRESET_OPTIONS.map((option) => {
          const selected = value === option.id;
          return (
            <button
              key={option.id}
              type="button"
              className={joinClassNames(
                'deployed-agents-context-presets__button',
                selected && 'deployed-agents-context-presets__button--selected',
              )}
              role="tab"
              aria-selected={selected}
              onClick={() => onSelect(option.id)}
            >
              <strong className="deployed-agents-context-presets__title">{option.label}</strong>
              <span className="deployed-agents-context-presets__description">{option.description}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function StudioTemplateCard({
  template,
  onSelect,
  actionLabel = 'Create Business Agent',
}: {
  template: StudioTemplate;
  onSelect: (templateId: string) => void;
  actionLabel?: string;
}) {
  return (
    <button
      type="button"
      className="studio-template-card"
      onClick={() => onSelect(template.id)}
    >
      <span className="studio-template-card__topline">
        <span className="studio-template-card__icon" aria-hidden="true">{template.icon}</span>
        <span className="studio-template-card__setup">{template.setupTime}</span>
      </span>
      <span className="studio-template-card__copy">
        <span className="studio-template-card__category">{template.category}</span>
        <strong className="studio-template-card__title">{template.title}</strong>
        <span className="studio-template-card__outcome">{template.outcome}</span>
      </span>
      <span className="studio-template-card__tags">
        {template.requiredConnectors.slice(0, 3).map((connector) => (
          <span key={connector} className="studio-template-card__tag">{connector}</span>
        ))}
      </span>
      <span className="studio-template-card__action">{actionLabel}</span>
    </button>
  );
}

export function DeployedAgentsSkeleton() {
  return (
    <WorkstationSplitWorkbench
      ariaLabel="Business Agents"
      className="studio-agents-workbench"
      sidebar={(
        <ListDetailPanel eyebrow="Business Agents" title="Loading workers">
          <div className="app-stack-3">
            <SkeletonBlock height="3.5rem" />
            <SkeletonBlock height="3.5rem" />
            <SkeletonBlock height="3.5rem" />
            <SkeletonBlock height="3.5rem" />
          </div>
        </ListDetailPanel>
      )}
    >
      <div className="app-stack-4">
        <ListDetailPanel eyebrow="Detail" title="Loading Business Agent details">
          <div className="app-stack-4">
            <SkeletonBlock height="2rem" width="60%" />
            <AppSurfaceStatGrid>
              <SkeletonBlock height="4rem" />
              <SkeletonBlock height="4rem" />
              <SkeletonBlock height="4rem" />
              <SkeletonBlock height="4rem" />
            </AppSurfaceStatGrid>
            <SkeletonBlock height="8rem" />
            <SkeletonBlock height="12rem" />
          </div>
        </ListDetailPanel>
      </div>
    </WorkstationSplitWorkbench>
  );
}

export function TranscriptEntryCard({
  entry,
}: {
  entry: TimelineEntry;
}) {
  const runId = readOptionalString(entry.run_id);
  const threadId = readOptionalString(entry.thread_id);
  return (
    <article className="deployed-agents-transcript-card">
      <div className="deployed-agents-transcript-card__header">
        <div className="deployed-agents-transcript-card__copy">
          <strong className="deployed-agents-transcript-card__title">{transcriptEntryTitle(entry)}</strong>
          <span className="deployed-agents-transcript-card__timestamp">
            {formatTimestamp(entry.ts)}
          </span>
        </div>
        <DataBadge tone={transcriptEntryTone(entry)}>
          {humanizeToken(entry.kind, 'Event')}
        </DataBadge>
      </div>
      <div className="deployed-agents-transcript-card__body">
        {transcriptEntryBody(entry)}
      </div>
      {(runId || threadId || readOptionalString(entry.status)) ? (
        <div className="deployed-agents-transcript-card__meta">
          {runId ? <span>Run {runId}</span> : null}
          {threadId ? <span>Thread {threadId}</span> : null}
          {readOptionalString(entry.status) ? <span>{humanizeToken(entry.status, 'Logged')}</span> : null}
        </div>
      ) : null}
    </article>
  );
}

export function RuntimeModeSelector({
  value,
  options,
  hasGatewayOnlineTarget,
  hasCloudComputerAvailableTarget,
  onSelect,
}: {
  value: WizardState['runtimePlacement'];
  options: ReadonlyArray<typeof STUDIO_RUNTIME_OPTIONS[number]>;
  hasGatewayOnlineTarget: boolean;
  hasCloudComputerAvailableTarget: boolean;
  onSelect: (next: WizardState['runtimePlacement']) => void;
}) {
  const defaultOption = options.find((option) => option.value === STUDIO_DEFAULT_RUNTIME_PLACEMENT) ?? options[0];
  const specialOptions = options.filter((option) => option.value !== defaultOption.value);
  function renderRuntimeCard(option: typeof STUDIO_RUNTIME_OPTIONS[number]) {
    const selected = value === option.value;
    const statuses = resolveRuntimeCardStatus(option, hasGatewayOnlineTarget, hasCloudComputerAvailableTarget);
    return (
      <button
        key={option.value}
        type="button"
        aria-pressed={selected}
        className={joinClassNames(
          'deployed-agents-wizard__tool-card',
          'deployed-agents-wizard__runtime-card',
          selected && 'deployed-agents-wizard__tool-card--selected',
        )}
        onClick={() => onSelect(normalizeRuntimePlacement(option.value))}
      >
        <div className="app-inline-actions app-inline-actions--tight">
          <strong>{option.label}</strong>
          {statuses.map((status) => (
            <DataBadge key={`${option.value}:${status.label}`} tone={status.tone}>
              {status.label}
            </DataBadge>
          ))}
        </div>
        <small>{option.hint}</small>
        <dl className="deployed-agents-wizard__runtime-metadata">
          <div>
            <dt>Runtime role</dt>
            <dd>{option.capabilities}</dd>
          </div>
          <div>
            <dt>Where it runs</dt>
            <dd>{option.runsWhere}</dd>
          </div>
          <div>
            <dt>Privacy level</dt>
            <dd>{option.privacy}</dd>
          </div>
          <div>
            <dt>Cost posture</dt>
            <dd>{option.costRisk}</dd>
          </div>
          <div>
            <dt>Required setup</dt>
            <dd>{option.setup}</dd>
          </div>
          <div>
            <dt>Best use cases</dt>
            <dd>{option.bestFor}</dd>
          </div>
        </dl>
      </button>
    );
  }
  return (
    <div className="app-stack-3">
      <div className="deployed-agents-wizard__runtime-grid">
        {renderRuntimeCard(defaultOption)}
      </div>
      {specialOptions.length > 0 ? (
        <section className="studio-actions__section studio-actions__section--compact" aria-label="Agent Computer options">
          <div className="studio-actions__section-head">
            <div>
              <span>Agent Computer options</span>
              <strong>Use only when this worker needs a computer or customer-owned infrastructure</strong>
            </div>
          </div>
          <div className="deployed-agents-wizard__runtime-grid">
            {specialOptions.map(renderRuntimeCard)}
          </div>
        </section>
      ) : null}
    </div>
  );
}

export function AgentSafetySummary({
  approvalMode,
  memoryEnabled,
  monthlyCostCapUsd,
  dailyMessageLimit,
}: {
  approvalMode: WizardState['approvalMode'];
  memoryEnabled: boolean;
  monthlyCostCapUsd: string;
  dailyMessageLimit: string;
}) {
  return (
    <FormGrid columns="repeat(auto-fit, minmax(12rem, 1fr))">
      <FormReadout label="Approval posture" value={humanizeToken(approvalMode, 'Balanced')} />
      <FormReadout label="Memory policy" value={memoryEnabled ? 'Enabled' : 'Disabled'} />
      <FormReadout label="Monthly spend cap" value={monthlyCostCapUsd.trim() || 'Not set'} />
      <FormReadout label="Daily message limit" value={dailyMessageLimit.trim() || 'Not set'} />
    </FormGrid>
  );
}

export function AgentLaunchChecklist({
  hasGatewayOnlineTarget,
  hasCloudComputerAvailableTarget,
  state,
}: {
  hasGatewayOnlineTarget: boolean;
  hasCloudComputerAvailableTarget: boolean;
  state: WizardState;
}) {
  const runtimeModeValid = state.runtimePlacement !== 'hosted_hardware_pool'
    || hasCloudComputerAvailableTarget;
  const selectedRuntime = studioRuntimeOption(state.runtimePlacement);
  const hasInstructions = state.systemPrompt.trim().length > 0;
  const usesHostedCredits = state.aiTier === 'light' || state.aiTier === 'pro' || state.aiTier === 'max';
  const hasModelRoute = usesHostedCredits || (state.providerId.trim().length > 0 && state.modelId.trim().length > 0);
  const hasChannel = state.customerChannel === 'draft' || state.telegramEnabled || state.customerChannel !== 'telegram';
  const checks = [
    { id: 'runtime', label: `${selectedRuntime.label} runtime ready`, ok: Boolean(state.runtimePlacement) && runtimeModeValid },
    { id: 'gateway', label: 'Customer computer connected', ok: state.runtimePlacement !== 'customer_local' || hasGatewayOnlineTarget },
    { id: 'model', label: 'AI route ready', ok: hasModelRoute },
    { id: 'instructions', label: 'Instructions present', ok: hasInstructions },
    { id: 'channel', label: 'Customer channel selected', ok: hasChannel },
    { id: 'tools', label: 'Action permissions reviewed', ok: true },
    { id: 'memory', label: 'Customer memory policy reviewed', ok: true },
    { id: 'approval', label: 'Approval policy valid', ok: Boolean(state.approvalMode) },
    { id: 'limits', label: 'Message limits active', ok: state.dailyMessageLimit.trim().length > 0 },
    { id: 'audit', label: 'Audit active', ok: true },
  ];
  return (
    <div className="studio-template-detail__group">
      <span className="studio-template-detail__label">Launch checklist</span>
      <ul className="studio-template-detail__checklist">
        {checks.map((item) => (
          <li key={item.id}>{item.ok ? 'Ready' : 'Pending'} - {item.label}</li>
        ))}
      </ul>
    </div>
  );
}

export function LaunchReadinessChecklist({
  items,
}: {
  items: LaunchReadinessItem[];
}) {
  const readyCount = items.filter((item) => item.ok).length;
  return (
    <section className="studio-actions__section studio-actions__section--compact" aria-label="Launch checklist">
      <div className="studio-actions__section-head">
        <div>
          <span>Launch checklist</span>
          <strong>{readyCount} of {items.length} checks ready</strong>
        </div>
      </div>
      <div className="studio-actions__skills">
        {items.map((item) => (
          <article key={item.id} className={joinClassNames('studio-actions__skill', item.ok && 'studio-actions__skill--ready')}>
            <div className="studio-actions__skill-copy">
              <strong>{item.label}</strong>
              <p>{item.detail}</p>
            </div>
            <span className={joinClassNames('studio-actions__status', item.ok ? 'studio-actions__status--ready' : 'studio-actions__status--blocked')}>
              {item.ok ? 'Ready' : 'Needs setup'}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}
