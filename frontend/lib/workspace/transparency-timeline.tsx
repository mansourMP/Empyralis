"use client";

/**
 * AgentTransparencyTimeline — renders backend transparency_events as
 * compact activity pills or an expandable "What happened" timeline.
 *
 * Never exposes raw model reasoning, private memory contents, or
 * internal policy jargon.  Uses the safe payloads already produced
 * by AgentTransparencyEvent.to_user_payload().
 */

import React, { useState } from "react";

// ── types ────────────────────────────────────────────────────────────

export interface TransparencyEventPayload {
  event_id?: string;
  trace_id?: string;
  event_type: string;
  title: string;
  summary?: string;
  status?: string;
  timestamp?: string;
  tool_name?: string;
  channel?: string;
}

// ── event-type → compact label ──────────────────────────────────────

const COMPACT_LABELS: Record<string, string> = {
  user_message_received: "Message received",
  memory_loaded: "Memory loaded",
  memory_excluded: "Memory excluded",
  planning_started: "Planning",
  tool_selected: "Tool selected",
  tool_started: "Using tool",
  tool_completed: "Action complete",
  tool_failed: "Action failed",
  gateway_action_started: "Computer action started",
  gateway_action_completed: "Computer action complete",
  channel_message_sent: "Message sent",
  channel_message_received: "Message received",
  policy_blocked: "Action blocked",
  quota_blocked: "Rate limited",
  unsafe_url_blocked: "URL blocked",
  final_response_started: "Replying",
  final_response_sent: "Response sent",
};

function eventPillLabel(evt: TransparencyEventPayload): string {
  return COMPACT_LABELS[evt.event_type] ?? evt.title;
}

// ── status colour ───────────────────────────────────────────────────

function statusColor(status?: string): string {
  switch (status) {
    case "running":
      return "var(--color-accent, var(--app-accent, #3a3a3a))";
    case "completed":
      return "var(--color-ok, #22c55e)";
    case "failed":
      return "var(--color-error, #ef4444)";
    case "blocked":
      return "var(--color-warn, #f59e0b)";
    case "denied":
      return "var(--color-error, #ef4444)";
    default:
      return "var(--color-muted, #6b7280)";
  }
}

// ── component ───────────────────────────────────────────────────────

export function AgentTransparencyTimeline({
  events,
  expanded = false,
}: {
  events: TransparencyEventPayload[];
  expanded?: boolean;
}) {
  if (!events || events.length === 0) return null;

  const [open, setOpen] = useState(expanded);

  return (
    <div className="agent-transparency-timeline" style={{ marginTop: 8 }}>
      {/* compact pills row */}
      <div
        className="transparency-pills"
        style={{ display: "flex", flexWrap: "wrap", gap: 6, cursor: "pointer" }}
        onClick={() => setOpen(!open)}
      >
        {events.map((evt, idx) => (
          <span
            key={evt.event_id ?? idx}
            className="transparency-pill"
            title={evt.summary ?? evt.title}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "2px 8px",
              borderRadius: 12,
              fontSize: 12,
              lineHeight: "18px",
              background: statusColor(evt.status) + "18",
              color: statusColor(evt.status),
              border: `1px solid ${statusColor(evt.status)}40`,
            }}
          >
            <span
              className="pill-dot"
              style={{
                width: 6,
                height: 6,
                borderRadius: 3,
                background: statusColor(evt.status),
                flexShrink: 0,
              }}
            />
            {eventPillLabel(evt)}
          </span>
        ))}
        <span style={{ fontSize: 12, color: "var(--color-muted, #6b7280)", padding: "2px 4px" }}>
          {open ? "▲" : "▼"} What happened
        </span>
      </div>

      {/* expandable timeline */}
      {open && (
        <div className="transparency-timeline-detail" style={{ marginTop: 8, paddingLeft: 4 }}>
          {events.map((evt, idx) => (
            <div
              key={evt.event_id ?? idx}
              className="timeline-item"
              style={{
                display: "flex",
                gap: 10,
                padding: "4px 0",
                borderLeft: `2px solid ${statusColor(evt.status)}40`,
                paddingLeft: 12,
                marginBottom: 4,
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{evt.title}</div>
                {evt.summary && (
                  <div style={{ fontSize: 12, color: "var(--color-muted, #6b7280)", marginTop: 2 }}>
                    {evt.summary}
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, marginTop: 2, fontSize: 11, color: "var(--color-muted-faint, #9ca3af)" }}>
                  {evt.tool_name && <span>Tool: {evt.tool_name}</span>}
                  {evt.channel && <span>Channel: {evt.channel}</span>}
                  <span>{evt.status}</span>
                  {evt.timestamp && <span>{new Date(evt.timestamp).toLocaleTimeString()}</span>}
                </div>
              </div>
            </div>
          ))}
          {events[0]?.trace_id && (
            <CopyTraceIdButton traceId={events[0].trace_id} />
          )}
        </div>
      )}
    </div>
  );
}

function CopyTraceIdButton({ traceId }: { traceId: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      type="button"
      onClick={async (e) => {
        e.stopPropagation();
        try {
          await navigator.clipboard.writeText(traceId);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        } catch {
          // clipboard may not be available — silently ignore
        }
      }}
      style={{
        marginTop: 6,
        padding: "2px 8px",
        fontSize: 11,
        background: "transparent",
        border: "1px solid var(--color-border, #e5e7eb)",
        borderRadius: 6,
        cursor: "pointer",
        color: "var(--color-muted, #6b7280)",
      }}
    >
      {copied ? "Copied!" : `Trace: ${traceId.slice(0, 8)}…`}
    </button>
  );
}
