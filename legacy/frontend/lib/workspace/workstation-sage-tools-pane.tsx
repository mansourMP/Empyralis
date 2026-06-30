'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ArrowDownToLine,
  CheckCircle2,
  Code2,
  FileCode2,
  FolderPlus,
  KeyRound,
  PackageCheck,
  ShieldCheck,
  TerminalSquare,
  X,
} from 'lucide-react';

import { AppNotice, joinClassNames } from '@/lib/ui/primitives';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';

type SageSkillRecord = {
  id: string;
  name: string;
  description: string;
  whatItDoes: string;
  status: string;
  statusLabel: string;
  reason: string | null;
  setupRequirement: string | null;
  source: string | null;
  tools: string[];
  slashCommands: string[];
  permissionLabel: string | null;
  actionClass: string | null;
  requiresApproval: boolean;
  skillBody: string | null;
  readme: string | null;
  curated: boolean;
  activeNow: boolean;
  supportedOs: string[];
  allowedRuntimeModes: string[];
};

type SageSkillsSummary = {
  totalCount: number;
  readyCount: number;
  needsSetupCount: number;
  unsupportedCount: number;
  disabledCount: number;
};

type SageCapabilitiesSummary = {
  totalCount: number;
  readyCount: number;
  needsSetupCount: number;
  needsApprovalCount: number;
  memoryCount: number;
  skillCount: number;
  mcpCount: number;
};

type SageHealthCheckRecord = {
  id: string;
  label: string;
  status: string;
  surface: string;
};

type SkillPreviewMode = 'preview' | 'source';

function readString(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

function readStringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const text = readString(item);
        return text ? [text] : [];
      })
    : [];
}

function readNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function normalizeSkill(item: unknown): SageSkillRecord | null {
  const record = item && typeof item === 'object' ? item as Record<string, unknown> : {};
  const id = readString(record.id);
  const name = readString(record.name, id || 'Skill');
  if (!id && !name) {
    return null;
  }
  return {
    id: id || name.toLowerCase().replace(/\s+/g, '-'),
    name,
    description: readString(record.description),
    whatItDoes: readString(record.what_it_does, readString(record.description)),
    status: readString(record.status, 'needs_setup'),
    statusLabel: readString(record.status_label, 'Needs setup'),
    reason: readString(record.reason) || null,
    setupRequirement: readString(record.setup_requirement) || null,
    source: readString(record.source) || null,
    tools: readStringList(record.tools),
    slashCommands: readStringList(record.slash_commands),
    permissionLabel: readString(record.permission_label) || null,
    actionClass: readString(record.action_class) || null,
    requiresApproval: record.requires_approval === true,
    skillBody: readString(record.skill_body) || null,
    readme: readString(record.readme) || null,
    curated: record.curated === true,
    activeNow: record.active_now === true,
    supportedOs: readStringList(record.supported_os),
    allowedRuntimeModes: readStringList(record.allowed_runtime_modes),
  };
}

function normalizeSummary(payload: Record<string, unknown>): SageSkillsSummary {
  const summary = payload.summary && typeof payload.summary === 'object'
    ? payload.summary as Record<string, unknown>
    : {};
  return {
    totalCount: readNumber(summary.total_count),
    readyCount: readNumber(summary.ready_count),
    needsSetupCount: readNumber(summary.needs_setup_count),
    unsupportedCount: readNumber(summary.unsupported_count),
    disabledCount: readNumber(summary.disabled_count),
  };
}

function normalizeSkillsPayload(payload: unknown): {
  items: SageSkillRecord[];
  summary: SageSkillsSummary;
} {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  const items = Array.isArray(record.items)
    ? record.items.flatMap((item) => {
        const skill = normalizeSkill(item);
        return skill ? [skill] : [];
      })
    : [];
  return {
    items,
    summary: normalizeSummary(record),
  };
}

function normalizeCapabilitiesSummary(payload: Record<string, unknown>): SageCapabilitiesSummary {
  const summary = payload.summary && typeof payload.summary === 'object'
    ? payload.summary as Record<string, unknown>
    : {};
  return {
    totalCount: readNumber(summary.total_count),
    readyCount: readNumber(summary.ready_count),
    needsSetupCount: readNumber(summary.needs_setup_count),
    needsApprovalCount: readNumber(summary.needs_approval_count),
    memoryCount: readNumber(summary.memory_count),
    skillCount: readNumber(summary.skill_count),
    mcpCount: readNumber(summary.mcp_count),
  };
}

function normalizeHealthChecks(payload: Record<string, unknown>): SageHealthCheckRecord[] {
  return Array.isArray(payload.health_checks)
    ? payload.health_checks.flatMap((item) => {
      const record = item && typeof item === 'object' ? item as Record<string, unknown> : {};
      const id = readString(record.id);
      const label = readString(record.label);
      if (!id || !label) {
        return [];
      }
      return [{
        id,
        label,
        status: readString(record.status, 'needs_setup'),
        surface: readString(record.surface),
      }];
    })
    : [];
}

function normalizeCapabilitiesPayload(payload: unknown): {
  summary: SageCapabilitiesSummary;
  healthChecks: SageHealthCheckRecord[];
} {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  return {
    summary: normalizeCapabilitiesSummary(record),
    healthChecks: normalizeHealthChecks(record),
  };
}

function statusTone(status: string): 'ready' | 'blocked' | 'neutral' {
  if (status === 'ready' || status === 'available') {
    return 'ready';
  }
  if (status === 'unsupported_device' || status === 'disabled_policy') {
    return 'blocked';
  }
  return 'neutral';
}

function skillIcon(skill: SageSkillRecord) {
  const id = skill.id.toLowerCase();
  if (id.includes('1password') || id.includes('password') || id.includes('vault')) {
    return KeyRound;
  }
  if (id.includes('tmux') || id.includes('terminal')) {
    return TerminalSquare;
  }
  if (id.includes('notes') || id.includes('reminder')) {
    return FileCode2;
  }
  if (skill.activeNow) {
    return CheckCircle2;
  }
  return PackageCheck;
}

function skillFileName(skill: SageSkillRecord): string {
  const id = skill.id.trim() || skill.name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  return `${id}.skill.md`;
}

function skillSource(skill: SageSkillRecord): string {
  if (skill.skillBody) {
    return skill.skillBody;
  }
  if (skill.readme) {
    return skill.readme;
  }
  const lines = [
    '---',
    `id: ${skill.id}`,
    `name: ${skill.name}`,
    `status: ${skill.status}`,
    `source: ${skill.source || 'workspace'}`,
    `requires_approval: ${skill.requiresApproval ? 'true' : 'false'}`,
    skill.actionClass ? `action_class: ${skill.actionClass}` : null,
    skill.permissionLabel ? `permission: ${skill.permissionLabel}` : null,
    skill.supportedOs.length > 0 ? `supported_os: [${skill.supportedOs.join(', ')}]` : null,
    skill.allowedRuntimeModes.length > 0 ? `runtime_modes: [${skill.allowedRuntimeModes.join(', ')}]` : null,
    '---',
    '',
    `# ${skill.name}`,
    '',
    skill.whatItDoes || skill.description || 'Reusable Sage capability package.',
    '',
    '## Commands',
    '',
    ...(skill.slashCommands.length > 0 ? skill.slashCommands.map((command) => `- \`${command}\``) : ['- No slash commands declared yet.']),
    '',
    '## Tools',
    '',
    ...(skill.tools.length > 0 ? skill.tools.map((tool) => `- \`${tool}\``) : ['- No tool handles declared yet.']),
    '',
    '## Setup',
    '',
    skill.setupRequirement || skill.reason || 'No additional setup declared.',
    '',
    '## Safety',
    '',
    skill.requiresApproval
      ? 'Sensitive actions from this skill require approval before execution.'
      : 'This skill follows the workspace capability policy before execution.',
  ].filter((line): line is string => line !== null);
  return lines.join('\n');
}

export function WorkstationSageToolsPane() {
  const services = useWorkspaceServices();
  const [skills, setSkills] = useState<SageSkillRecord[]>([]);
  const [summary, setSummary] = useState<SageSkillsSummary>({
    totalCount: 0,
    readyCount: 0,
    needsSetupCount: 0,
    unsupportedCount: 0,
    disabledCount: 0,
  });
  const [capabilitySummary, setCapabilitySummary] = useState<SageCapabilitiesSummary>({
    totalCount: 0,
    readyCount: 0,
    needsSetupCount: 0,
    needsApprovalCount: 0,
    memoryCount: 0,
    skillCount: 0,
    mcpCount: 0,
  });
  const [healthChecks, setHealthChecks] = useState<SageHealthCheckRecord[]>([]);
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const [previewMode, setPreviewMode] = useState<SkillPreviewMode>('preview');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    void Promise.all([
      services.client.listSageSkills(),
      services.client.listSageCapabilities(),
    ])
      .then(([skillsPayload, capabilitiesPayload]) => {
        if (cancelled) {
          return;
        }
        const normalized = normalizeSkillsPayload(skillsPayload);
        const capabilities = normalizeCapabilitiesPayload(capabilitiesPayload);
        setSkills(normalized.items);
        setSummary(normalized.summary);
        setCapabilitySummary(capabilities.summary);
        setHealthChecks(capabilities.healthChecks);
        setSelectedSkillId((current) => (
          current && normalized.items.some((item) => item.id === current) ? current : null
        ));
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Sage skills are unavailable right now.');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [services.client]);

  useEffect(() => {
    if (!selectedSkillId) {
      return undefined;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSelectedSkillId(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedSkillId]);

  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.id === selectedSkillId) ?? null,
    [selectedSkillId, skills],
  );

  return (
    <div className="sage-skills-panel">
      {error ? (
        <AppNotice tone="warning">Skill packages could not load. Try again when the workspace is ready.</AppNotice>
      ) : null}

      <section className="sage-skills-library" aria-label="Sage runtime readiness">
        <header className="sage-skills-library__header">
          <div>
            <p className="sage-unified-section__label">Runtime readiness</p>
            <h3>Workspace tools after policy checks.</h3>
          </div>
          <div className="sage-skills-library__stats" aria-label="Runtime counts">
            <span>{capabilitySummary.readyCount} ready</span>
            <span>{capabilitySummary.needsApprovalCount} review</span>
            <span>{capabilitySummary.totalCount} total</span>
          </div>
        </header>
        <div className="sage-skills-install" aria-label="Runtime class counts">
          <div className="sage-skills-install__copy">
            <strong>Memory, Skill.md, and MCP stay permissioned</strong>
            <span>Sage follows the enabled workspace policy and session runtime.</span>
          </div>
          <div className="sage-skills-install__actions">
            <span className="sage-skills-install__option">{capabilitySummary.memoryCount} memory</span>
            <span className="sage-skills-install__option">{capabilitySummary.skillCount} skills</span>
            <span className="sage-skills-install__option">{capabilitySummary.mcpCount} MCP</span>
          </div>
        </div>
        {healthChecks.length > 0 ? (
          <div className="sage-skills-install" aria-label="Capability health checks">
            <div className="sage-skills-install__copy">
              <strong>Health checks</strong>
              <span>Setup state is shown here instead of being injected into chat.</span>
            </div>
            <div className="sage-skills-install__actions">
              {healthChecks.map((check) => (
                <span
                  key={check.id}
                  className={joinClassNames(
                    'sage-skills-install__option',
                    statusTone(check.status) === 'ready' && 'sage-skills-install__option--ready',
                  )}
                >
                  {check.label}: {check.status.replace(/_/g, ' ')}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="sage-skills-library" aria-label="Skill.md package library">
        <header className="sage-skills-library__header">
          <div>
            <p className="sage-unified-section__label">Skill.md library</p>
            <h3>Reusable packages Sage can call after review.</h3>
          </div>
          <div className="sage-skills-library__stats" aria-label="Skill package counts">
            <span>{summary.readyCount} ready</span>
            <span>{summary.needsSetupCount} setup</span>
            <span>{summary.totalCount} total</span>
          </div>
        </header>

        <div className="sage-skills-install" aria-label="Add skill package options">
          <div className="sage-skills-install__copy">
            <strong>Install Skill.md packages</strong>
            <span>Workspace, local, and bundled folders with a `SKILL.md` file appear here with real setup and safety state.</span>
          </div>
          <div className="sage-skills-install__actions">
            <span className="sage-skills-install__option sage-skills-install__option--ready">
              <FolderPlus size={15} aria-hidden="true" />
              Local folders ready
            </span>
            <span className="sage-skills-install__option">
              <ArrowDownToLine size={15} aria-hidden="true" />
              GitHub import not available yet
            </span>
          </div>
        </div>

        {isLoading ? (
          <div className="sage-settings-empty">Loading Sage skill packages…</div>
        ) : null}

        {!isLoading && skills.length > 0 ? (
          <div className="sage-skills-file-list" role="listbox" aria-label="Skill package files">
            {skills.map((skill) => {
              const Icon = skillIcon(skill);
              const tone = statusTone(skill.status);
              const selected = selectedSkillId === skill.id;
              return (
                <button
                  key={skill.id}
                  type="button"
                  className={joinClassNames(
                    'sage-skills-file',
                    selected && 'sage-skills-file--active',
                  )}
                  aria-selected={selected}
                  role="option"
                  onClick={() => {
                    setSelectedSkillId(skill.id);
                    setPreviewMode('preview');
                  }}
                >
                  <span className="sage-skills-file__icon" aria-hidden="true">
                    <Icon size={17} />
                  </span>
                  <span className="sage-skills-file__copy">
                    <span className="sage-skills-file__eyebrow">
                      {skill.curated ? 'Curated Skill.md' : 'Installed Skill.md'}
                    </span>
                    <strong>{skillFileName(skill)}</strong>
                    <small>{skill.whatItDoes || skill.description || 'Reusable Sage capability package.'}</small>
                  </span>
                  <span
                    className={joinClassNames(
                      'sage-skills-file__status',
                      tone === 'ready' && 'sage-skills-file__status--ready',
                      tone === 'blocked' && 'sage-skills-file__status--blocked',
                    )}
                  >
                    {skill.statusLabel}
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}

        {!isLoading && skills.length === 0 ? (
          <div className="sage-settings-empty">
            No Skill.md packages are installed yet. Add a custom skill when Sage needs a reusable procedure.
          </div>
        ) : null}
      </section>

      {selectedSkill ? (
        <div
          className="sage-skill-modal"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setSelectedSkillId(null);
            }
          }}
        >
          <SkillDocumentPreview
            skill={selectedSkill}
            mode={previewMode}
            onModeChange={setPreviewMode}
            onClose={() => setSelectedSkillId(null)}
          />
        </div>
      ) : null}
    </div>
  );
}

function SkillDocumentPreview({
  skill,
  mode,
  onModeChange,
  onClose,
}: {
  skill: SageSkillRecord;
  mode: SkillPreviewMode;
  onModeChange: (mode: SkillPreviewMode) => void;
  onClose: () => void;
}) {
  const tone = statusTone(skill.status);
  const metadata = [
    skill.source === 'curated_pack' ? 'Curated' : skill.source,
    skill.permissionLabel,
    skill.actionClass,
    skill.requiresApproval ? 'Approval required' : 'Policy governed',
  ].flatMap((item) => (item ? [item] : []));
  return (
    <section
      className="sage-skill-document"
      role="dialog"
      aria-modal="true"
      aria-label={`${skill.name} Skill.md preview`}
    >
      <header className="sage-skill-document__header">
        <div>
          <p className="sage-unified-section__label">{skillFileName(skill)}</p>
          <h3>{skill.name}</h3>
        </div>
        <button type="button" className="sage-skill-document__close" aria-label="Close skill preview" onClick={onClose}>
          <X size={17} />
        </button>
      </header>

      <div className="sage-skill-document__body">
        <div className="sage-skill-document__mode" aria-label="Skill document mode">
          <button
            type="button"
            className={joinClassNames('sage-skill-document__mode-button', mode === 'preview' && 'sage-skill-document__mode-button--active')}
            onClick={() => onModeChange('preview')}
          >
            <PackageCheck size={16} />
            Preview
          </button>
          <button
            type="button"
            className={joinClassNames('sage-skill-document__mode-button', mode === 'source' && 'sage-skill-document__mode-button--active')}
            onClick={() => onModeChange('source')}
          >
            <Code2 size={16} />
            Source
          </button>
        </div>

        {mode === 'source' ? (
          <pre className="sage-skill-document__source">{skillSource(skill)}</pre>
        ) : (
          <article className="sage-skill-document__preview">
            <div
              className={joinClassNames(
                'sage-skill-document__status',
                tone === 'ready' && 'sage-skill-document__status--ready',
                tone === 'blocked' && 'sage-skill-document__status--blocked',
              )}
            >
              <span aria-hidden="true" />
              {skill.statusLabel}
            </div>
            <p>{skill.whatItDoes || skill.description || 'Reusable Sage capability package.'}</p>
            {metadata.length > 0 ? (
              <div className="sage-skill-document__chips">
                {metadata.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            ) : null}
            <section>
              <h4>Commands</h4>
              {skill.slashCommands.length > 0 ? (
                <ul>
                  {skill.slashCommands.map((command) => (
                    <li key={command}>{command}</li>
                  ))}
                </ul>
              ) : (
                <p>No slash commands declared yet.</p>
              )}
            </section>
            <section>
              <h4>Tools</h4>
              {skill.tools.length > 0 ? (
                <ul>
                  {skill.tools.map((tool) => (
                    <li key={tool}>{tool}</li>
                  ))}
                </ul>
              ) : (
                <p>No tool handles declared yet.</p>
              )}
            </section>
            {skill.reason || skill.setupRequirement ? (
              <section className="sage-skill-document__setup">
                <h4>
                  <ShieldCheck size={15} aria-hidden="true" />
                  Setup
                </h4>
                <p>{skill.reason || skill.setupRequirement}</p>
              </section>
            ) : null}
          </article>
        )}
      </div>
    </section>
  );
}
