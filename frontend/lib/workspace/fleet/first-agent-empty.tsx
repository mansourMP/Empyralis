"use client";

import { useState } from "react";
import { Bot } from "lucide-react";

import { FleetCreateAgentWizard } from "./FleetCreateAgentWizard";

/**
 * The single first-run call to action, shared by every fresh-workspace empty
 * state (Agents, Projects, Inbox): "create your first agent" → the wizard.
 * One action, non-technical copy, no tour.
 */
export function FirstAgentEmpty({
  title,
  desc,
  onCreate,
}: {
  title: string;
  desc: string;
  onCreate: () => void;
}) {
  return (
    <div className="fleet-empty">
      <div className="fleet-empty-icon">
        <Bot size={20} strokeWidth={1.75} />
      </div>
      <div className="fleet-empty-title">{title}</div>
      <div className="fleet-empty-desc">{desc}</div>
      <div className="fleet-empty-actions">
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={onCreate}>
          Create your first agent
        </button>
      </div>
    </div>
  );
}

/** Self-contained: the first-run empty state plus its own wizard modal. */
export function CreateFirstAgentEmpty({
  workspaceId,
  onCreated,
  title,
  desc,
}: {
  workspaceId: string;
  onCreated?: () => void;
  title: string;
  desc: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <FirstAgentEmpty title={title} desc={desc} onCreate={() => setOpen(true)} />
      {open && (
        <FleetCreateAgentWizard
          workspaceId={workspaceId}
          onClose={() => setOpen(false)}
          onCreated={() => {
            setOpen(false);
            onCreated?.();
          }}
        />
      )}
    </>
  );
}
