"use client";

/**
 * Labels, wherever a task is drawn.
 *
 * WHY THIS IS ITS OWN FILE, beside task-status.tsx. That file owns the task
 * vocabulary the PRODUCT defines — seven statuses, five priorities, both
 * closed sets with hand-authored geometry. A label is the opposite kind of
 * thing: a per-workspace vocabulary a human invents at runtime, with a name
 * of unknown length and a colour chosen from a palette. Same neighbourhood,
 * different rules, so it gets its own module rather than swelling the one
 * that documents "a fill fraction is geometry, a colour is a theme decision".
 *
 * COLOUR IS A TOKEN NAME, NOT A HEX. `label.color` is one of
 * fleet-data.LABEL_COLORS ('grey' | 'red' | … ), and the theme decides what
 * each token looks like — exactly the discipline task-status.tsx follows for
 * --task-*. It is deliberate and not an oversight: a colour picked while
 * looking at the dark theme is routinely unreadable in the light one, so the
 * human picks a NAME and each theme resolves it. Nothing here emits a hex.
 * Resolution happens in CSS, through `.fleet-label-dot[data-color="…"]` →
 * `var(--label-…)`; an unrecognised token falls through to --label-grey
 * rather than rendering an invisible chip.
 *
 * THE CHIP IS A DOT PLUS NEUTRAL TEXT, not a colour-filled pill. Ten
 * saturated pills on one card would out-shout the status ring, which is the
 * one colour on that card that carries lifecycle meaning
 * (fleet-theme.css:12 — "colour there is information, not decoration"). The
 * dot is the colour channel; the name is the content.
 *
 * OVERFLOW. Every surface caps how many chips it draws and rolls the rest
 * into a "+N" chip whose tooltip names them, because a task with nine labels
 * must not be allowed to set the height of a board column. Callers pass the
 * cap: 2 on the dense board card, 3 in the wider row layouts.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Plus, Tag, X } from "lucide-react";

import {
  LABEL_COLORS,
  attachFleetTaskLabel,
  createFleetLabel,
  detachFleetTaskLabel,
  patchFleetLabel,
  useFleetLabels,
  type FleetLabel,
} from "./fleet-data";

/** One label. Read-only — the chip that can be REMOVED is the editor's own
 *  (below); a chip on a card must not grow a destructive control. */
export function TaskLabelChip({ label }: { label: FleetLabel }) {
  return (
    <span className="fleet-label-chip" title={label.name}>
      <span className="fleet-label-dot" data-color={label.color || "grey"} aria-hidden />
      <span className="fleet-label-chip-name">{label.name}</span>
    </span>
  );
}

/**
 * The ten palette tokens as clickable swatches — Linear's "pick a name, pick
 * a colour, done." Shared by every surface that MINTS a label (this file's
 * TaskLabelEditor, and TaskComposer.LabelChip) so the picker looks and
 * behaves identically wherever a human is naming a new label.
 *
 * Each swatch is just the standalone --label-* dot, larger and clickable —
 * no new colour values, the same ten tokens the chips already render.
 * Selection is a ring, not a size or fill change, so the row doesn't reflow
 * as the choice moves.
 */
export function LabelColorSwatches({
  value,
  onChange,
}: {
  value: string;
  onChange: (color: string) => void;
}) {
  return (
    <div className="fleet-label-swatches" role="radiogroup" aria-label="Label colour">
      {LABEL_COLORS.map((c) => (
        <button
          key={c}
          type="button"
          role="radio"
          aria-checked={value === c}
          aria-label={c}
          title={c}
          className={`fleet-label-swatch${value === c ? " is-selected" : ""}`}
          onClick={() => onChange(c)}
        >
          <span className="fleet-label-dot" data-color={c} aria-hidden />
        </button>
      ))}
    </div>
  );
}

/**
 * A task's labels, capped. Renders NOTHING when there are none — an empty
 * slot on every unlabelled card would cost a line of card height to say
 * nothing, and "no labels" is already legible from their absence.
 */
export function TaskLabelChips({
  labels,
  max = 2,
  className,
}: {
  /** `task.labels`, which is absent on a server without the labels migration
   *  — hence the optional/nullable type rather than a required array. */
  labels?: FleetLabel[] | null;
  max?: number;
  className?: string;
}) {
  const list = labels || [];
  if (list.length === 0) return null;

  // Only spend a slot on "+N" when it actually hides more than one chip:
  // with max=2 and three labels, "a, b, +1" and "a, b, c" are the same width,
  // so showing the third is strictly better.
  const shown = list.length <= max + 1 ? list : list.slice(0, max);
  const rest = list.slice(shown.length);

  return (
    <span className={`fleet-label-chips${className ? ` ${className}` : ""}`}>
      {shown.map((l) => (
        <TaskLabelChip key={l.id} label={l} />
      ))}
      {rest.length > 0 ? (
        <span
          className="fleet-label-chip fleet-label-chip--more"
          title={rest.map((l) => l.name).join(", ")}
        >
          +{rest.length}
        </span>
      ) : null}
    </span>
  );
}

/**
 * The editable version, for the task page's Properties column.
 *
 * Writes go straight out (attach/detach are their own endpoints — there is no
 * "labels" field on the task PATCH) and are painted optimistically first,
 * because the task itself is read from the 30s-polled task list: without the
 * optimistic pass a tick would sit there doing nothing for up to half a
 * minute. `onChanged` is the caller's refetch; the optimistic list is dropped
 * the moment it resolves, so the server always gets the last word — including
 * when the write failed and the label snaps back.
 *
 * It can MINT a label, like the composer can. That used to be the composer's
 * exclusive affordance (agents must never invent labels — an attach that
 * creates on a typo turns the vocabulary into a junk drawer — so the control
 * has to live on a human surface). This is also a human surface, and a picker
 * that can filter but not create is a dead end in a workspace that has no
 * labels yet.
 */
export function TaskLabelEditor({
  workspaceId,
  taskId,
  labels,
  onChanged,
}: {
  workspaceId: string;
  taskId: string;
  /** The task's current labels, straight off `task.labels`. */
  labels?: FleetLabel[] | null;
  onChanged: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Non-null only while a write is in flight — see the note above.
  const [optimistic, setOptimistic] = useState<FleetLabel[] | null>(null);
  // The colour a human has explicitly clicked for the label about to be
  // MINTED, overriding the round-robin default below. Cleared on every
  // successful create so the rotation resumes for the next one.
  const [mintColorOverride, setMintColorOverride] = useState<string | null>(null);
  // Which EXISTING label's swatch row is open for recolouring, or null.
  // Exactly one at a time — opening a second closes the first.
  const [recolorId, setRecolorId] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // The workspace vocabulary is only fetched once the picker is actually
  // opened; a task page that nobody labels costs no request.
  const { labels: vocabulary, refresh: refreshVocabulary } = useFleetLabels(workspaceId, open);

  const current = optimistic ?? labels ?? [];
  const currentIds = new Set(current.map((l) => l.id));

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Stopped so the page's own Escape handler (back to the board) does
        // not also fire — closing a menu and leaving the page on one keypress
        // is the classic double-dismiss bug.
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  // Closing the whole picker must not leave a swatch row silently open for
  // the next time it's reopened.
  useEffect(() => {
    if (!open) {
      setRecolorId(null);
      setMintColorOverride(null);
    }
  }, [open]);

  const write = useCallback(
    async (next: FleetLabel[], run: () => Promise<void>, failure: string) => {
      setError(null);
      setOptimistic(next);
      setBusy(true);
      try {
        await run();
        await onChanged();
      } catch (e) {
        setError(e instanceof Error ? e.message : failure);
      } finally {
        setBusy(false);
        setOptimistic(null);
      }
    },
    [onChanged],
  );

  const toggle = useCallback(
    (label: FleetLabel) => {
      if (busy) return;
      if (currentIds.has(label.id)) {
        void write(
          current.filter((l) => l.id !== label.id),
          () => detachFleetTaskLabel(workspaceId, taskId, label.id),
          "Could not remove that label.",
        );
      } else {
        void write(
          [...current, label],
          () => attachFleetTaskLabel(workspaceId, taskId, label.id),
          "Could not add that label.",
        );
      }
    },
    [busy, current, currentIds, taskId, workspaceId, write],
  );

  const q = query.trim().toLowerCase();
  const shown = q ? vocabulary.filter((l) => l.name.toLowerCase().includes(q)) : vocabulary;
  const exact = vocabulary.some((l) => l.name.toLowerCase() === q);

  // Round-robin off the palette so consecutive new labels differ by default —
  // the same rule the composer's LabelChip follows — but PRE-SELECTS rather
  // than silently applies: a human on the way to filing a task can still hit
  // Enter without ever looking at the swatches, but one who cares can click a
  // different one first. "Recolour it later" stopped being the only option
  // once there was somewhere to make the choice at all.
  const mintColorDefault = LABEL_COLORS[vocabulary.length % LABEL_COLORS.length];
  const mintColor = mintColorOverride ?? mintColorDefault;

  async function mint() {
    const name = query.trim();
    if (!name || busy) return;
    setError(null);
    setBusy(true);
    let created: FleetLabel;
    try {
      created = await createFleetLabel(workspaceId, { name, color: mintColor });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create that label.");
      setBusy(false);
      return;
    }
    try {
      await attachFleetTaskLabel(workspaceId, taskId, created.id);
    } catch (e) {
      // The label itself now exists in the workspace vocabulary — only
      // attaching it to THIS task failed. "Could not create that label"
      // would be a lie the vocabulary list (once refreshed) would visibly
      // contradict.
      setError(
        `"${name}" was created, but could not be added to this task${e instanceof Error ? `: ${e.message}` : "."}`,
      );
      setBusy(false);
      await refreshVocabulary().catch(() => {});
      return;
    }
    setQuery("");
    setMintColorOverride(null);
    // Both real mutations already happened — a failure to re-fetch the
    // vocabulary or the task's own label list must not report as "Could not
    // create that label."
    await refreshVocabulary().catch(() => {});
    await Promise.resolve(onChanged()).catch(() => {});
    setBusy(false);
  }

  // Recolour an EXISTING label (fleet_patch_label). Distinct from mint's
  // colour choice: this is a metadata edit on a label that may already sit on
  // other tasks, not a decision made once at creation.
  async function recolor(label: FleetLabel, color: string) {
    setRecolorId(null);
    if (color === (label.color || "grey") || busy) return;
    setError(null);
    try {
      await patchFleetLabel(workspaceId, label.id, { color });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not recolour that label.");
      return;
    }
    // The recolour already happened — a failed re-fetch must not be
    // reported as "Could not recolour that label."
    await refreshVocabulary().catch(() => {});
    await Promise.resolve(onChanged()).catch(() => {});
  }

  return (
    <div className="fleet-label-editor" ref={wrapRef}>
      <div className="fleet-label-editor-chips">
        {current.map((l) => (
          <span key={l.id} className="fleet-label-chip fleet-label-chip--removable" title={l.name}>
            <span className="fleet-label-dot" data-color={l.color || "grey"} aria-hidden />
            <span className="fleet-label-chip-name">{l.name}</span>
            <button
              type="button"
              className="fleet-label-chip-x"
              aria-label={`Remove label ${l.name}`}
              disabled={busy}
              onClick={() => toggle(l)}
            >
              <X size={10} strokeWidth={2.25} />
            </button>
          </span>
        ))}
        {/* The visible word is dropped once there are chips — a labelled task's
            row is already busy and the `+` alone is unambiguous next to them.
            The aria-label is NOT dropped with it: an icon-only button with no
            accessible name is just "button" to a screen reader. */}
        <button
          type="button"
          className={`fleet-label-add${open ? " is-open" : ""}`}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label="Add label"
          title="Add label"
          onClick={() => setOpen((v) => !v)}
        >
          <Plus size={11} strokeWidth={2.25} />
          {current.length === 0 ? "Add label" : null}
        </button>
      </div>

      {open ? (
        <div className="fleet-composer-pop fleet-label-pop" role="menu" aria-label="Labels">
          <div className="fleet-composer-pop-search">
            <input
              className="fleet-composer-pop-input"
              value={query}
              autoFocus
              placeholder="Filter or create…"
              aria-label="Filter or create a label"
              onChange={(e) => {
                setQuery(e.currentTarget.value);
                setRecolorId(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !exact && query.trim()) {
                  e.preventDefault();
                  void mint();
                }
              }}
            />
          </div>
          <div className="fleet-composer-pop-list">
            {shown.map((l) => (
              <div key={l.id} className="fleet-label-vocab-row">
                {/* The dot is its OWN button, pulled out of the name button
                    below rather than nested in it (a <button> cannot legally
                    contain another) — this is the recolour affordance, the
                    only place in the app one exists. Clicking it opens the
                    same ten swatches mint() uses; picking one PATCHes
                    fleet_patch_label and every task carrying this label
                    updates at once. */}
                <button
                  type="button"
                  className="fleet-label-swatch-trigger"
                  aria-label={`Change colour for ${l.name}`}
                  aria-haspopup="menu"
                  aria-expanded={recolorId === l.id}
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    setRecolorId((cur) => (cur === l.id ? null : l.id));
                  }}
                >
                  <span className="fleet-label-dot" data-color={l.color || "grey"} aria-hidden />
                </button>
                <button
                  type="button"
                  className="fleet-composer-pop-item fleet-label-vocab-name"
                  role="menuitemcheckbox"
                  aria-checked={currentIds.has(l.id)}
                  disabled={busy}
                  onClick={() => toggle(l)}
                >
                  <span className="fleet-composer-pop-label">{l.name}</span>
                  {currentIds.has(l.id) ? (
                    <Check size={13} strokeWidth={2} className="fleet-composer-pop-check" />
                  ) : null}
                </button>
                {recolorId === l.id ? (
                  <div className="fleet-label-recolor-pop">
                    <LabelColorSwatches
                      value={l.color || "grey"}
                      onChange={(c) => void recolor(l, c)}
                    />
                  </div>
                ) : null}
              </div>
            ))}
            {query.trim() && !exact ? (
              <>
                <LabelColorSwatches value={mintColor} onChange={setMintColorOverride} />
                <button
                  type="button"
                  className="fleet-composer-pop-item"
                  disabled={busy}
                  onClick={() => void mint()}
                >
                  <span className="fleet-composer-pop-icon">
                    <span className="fleet-label-dot" data-color={mintColor} aria-hidden />
                  </span>
                  <span className="fleet-composer-pop-label">
                    {busy ? "Working…" : `Create label “${query.trim()}”`}
                  </span>
                </button>
              </>
            ) : null}
            {shown.length === 0 && !query.trim() ? (
              <div className="fleet-composer-pop-empty">
                No labels in this workspace yet. Type a name to create the first one.
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {error ? <div className="fleet-label-editor-error">{error}</div> : null}
    </div>
  );
}

/** The Properties-column icon for the Labels row, so the row reads as a
 *  record like Status/Priority/Assignee rather than a loose chip pile. */
export function TaskLabelRowIcon() {
  return <Tag size={15} strokeWidth={1.75} />;
}
