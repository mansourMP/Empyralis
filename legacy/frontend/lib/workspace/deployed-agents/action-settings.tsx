'use client';

import { Plus } from 'lucide-react';

import { joinClassNames } from '@/lib/ui/primitives';
import {
  STUDIO_ACTION_SKILL_OPTIONS,
  STUDIO_TOOL_OPTIONS,
} from './constants';
import {
  toolLabel,
} from './utils';

export function AgentActionCapabilitySections({
  selectedToolIds,
  onToggleTool,
  onOpenIntegrations,
}: {
  selectedToolIds: string[];
  onToggleTool: (toolId: string) => void;
  onOpenIntegrations?: () => void;
}) {
  const selectedToolSet = new Set(selectedToolIds);
  const readySkills = STUDIO_ACTION_SKILL_OPTIONS.filter((skill) => skill.requiredToolIds.every((toolId) => selectedToolSet.has(toolId)));
  const blockedSkills = STUDIO_ACTION_SKILL_OPTIONS.length - readySkills.length;
  return (
    <div className="studio-actions">
      <div className="studio-actions__summary" aria-label="Actions summary">
        <div className="studio-actions__summary-copy">
          <span>Allowed actions</span>
          <strong>{readySkills.length} workflows ready</strong>
          <p>Playbooks are the customer-facing workflows. Action permissions decide which systems those workflows can use.</p>
        </div>
        <div className="studio-actions__summary-stats" aria-label="Action readiness">
          <div>
            <strong>{readySkills.length}</strong>
            <span>Playbooks</span>
          </div>
          <div>
            <strong>{blockedSkills}</strong>
            <span>Needs setup</span>
          </div>
          <div>
            <strong>{selectedToolIds.length}</strong>
            <span>Actions on</span>
          </div>
        </div>
      </div>
      <section className="studio-actions__section" aria-label="Skills and playbooks">
        <div className="studio-actions__section-head">
          <div>
            <span>Action playbooks</span>
            <strong>Workflows this Business Agent can perform</strong>
          </div>
          <small>Powered by enabled actions</small>
        </div>
        <div className="studio-actions__skills">
          {STUDIO_ACTION_SKILL_OPTIONS.map((skill) => {
            const missingToolIds = skill.requiredToolIds.filter((toolId) => !selectedToolSet.has(toolId));
            const ready = missingToolIds.length === 0;
            return (
              <article
                key={skill.id}
                className={joinClassNames('studio-actions__skill', ready && 'studio-actions__skill--ready')}
              >
                <div className="studio-actions__skill-copy">
                  <strong>{skill.label}</strong>
                  <p>{skill.description}</p>
                </div>
                <span className={joinClassNames('studio-actions__status', ready ? 'studio-actions__status--ready' : 'studio-actions__status--blocked')}>
                  {ready ? 'Ready' : `Needs ${missingToolIds.map((toolId) => toolLabel(toolId)).join(', ')}`}
                </span>
              </article>
            );
          })}
        </div>
      </section>
      <section className="studio-actions__section" aria-label="Action permissions">
        <div className="studio-actions__section-head">
          <div>
            <span>Actions</span>
            <strong>Actions this Business Agent is allowed to use</strong>
          </div>
          <div className="studio-actions__section-head-actions">
            <small>{selectedToolIds.length} enabled</small>
            {onOpenIntegrations ? (
              <button type="button" className="studio-actions__link-button" onClick={onOpenIntegrations}>
                <Plus aria-hidden="true" />
                Add action
              </button>
            ) : null}
          </div>
        </div>
        <div className="sage-tool-list" role="list">
          {STUDIO_TOOL_OPTIONS.map((tool) => {
            const selected = selectedToolSet.has(tool.id);
            return (
              <article key={tool.id} className={joinClassNames('sage-tool-row', selected && 'sage-tool-row--enabled')} role="listitem">
                <div className="sage-tool-row__identity">
                  <div className="sage-tool-row__icon" aria-hidden="true">{tool.label.slice(0, 1)}</div>
                  <div className="sage-tool-row__copy">
                    <strong className="sage-tool-row__title">{tool.label}</strong>
                    <p className="sage-tool-row__description">{tool.description}</p>
                  </div>
                </div>
                <button
                  type="button"
                  className={joinClassNames('sage-tool-toggle', selected && 'sage-tool-toggle--enabled')}
                  role="switch"
                  aria-checked={selected}
                  onClick={() => onToggleTool(tool.id)}
                >
                  <span className="sage-tool-toggle__thumb" />
                </button>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
