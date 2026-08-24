import Foundation

/// The canonical agent-trace event, mirroring `_canonical_trace_event` in
/// server_modules/routes_agent_traces.py field for field. Every field except
/// `id`, `seq` and `data` is nullable on the wire, so every one of them is
/// optional here — an unexpected shape must never crash the stream.
struct TraceEvent: Decodable, Identifiable {
    let id: String
    let traceId: String?
    let seq: Int
    let ts: String?
    let eventType: String?
    let persisted: Bool
    let agentId: String?
    let parentId: String?
    let itemId: String?
    let toolCallId: String?
    let childRunId: String?
    let approvalId: String?
    let artifactId: String?
    /// `payload` on the backend — an arbitrary JSON object whose shape varies
    /// per event type. Kept as loosely-typed JSON and read key by key; never
    /// modelled as a fixed struct, because a shape we did not anticipate must
    /// degrade to "render nothing", not to a decoding failure.
    let data: [String: JSONValue]

    private enum CodingKeys: String, CodingKey {
        case id, seq, ts, persisted, data
        case traceId = "trace_id"
        case eventType = "event_type"
        case agentId = "agent_id"
        case parentId = "parent_id"
        case itemId = "item_id"
        case toolCallId = "tool_call_id"
        case childRunId = "child_run_id"
        case approvalId = "approval_id"
        case artifactId = "artifact_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
        traceId = try? c.decodeIfPresent(String.self, forKey: .traceId)
        seq = (try? c.decode(Int.self, forKey: .seq)) ?? 0
        ts = try? c.decodeIfPresent(String.self, forKey: .ts)
        eventType = try? c.decodeIfPresent(String.self, forKey: .eventType)
        persisted = (try? c.decode(Bool.self, forKey: .persisted)) ?? false
        agentId = try? c.decodeIfPresent(String.self, forKey: .agentId)
        parentId = try? c.decodeIfPresent(String.self, forKey: .parentId)
        itemId = try? c.decodeIfPresent(String.self, forKey: .itemId)
        toolCallId = try? c.decodeIfPresent(String.self, forKey: .toolCallId)
        childRunId = try? c.decodeIfPresent(String.self, forKey: .childRunId)
        approvalId = try? c.decodeIfPresent(String.self, forKey: .approvalId)
        artifactId = try? c.decodeIfPresent(String.self, forKey: .artifactId)
        data = (try? c.decode([String: JSONValue].self, forKey: .data)) ?? [:]
    }

    var normalizedType: String {
        (eventType ?? "").lowercased()
    }

    /// Mirrors `_terminal_trace_event`. When one of these arrives the backend
    /// closes the stream, so the client must stop describing itself as live.
    var isTerminal: Bool {
        normalizedType == "trace.completed" || normalizedType == "trace.failed"
    }

    /// A string out of `data`, or nil. Never a placeholder guess.
    func string(_ key: String) -> String? {
        guard let value = data[key]?.stringValue else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    func nested(_ key: String) -> [String: JSONValue]? {
        data[key]?.objectValue
    }
}

/// A tiny, total JSON representation. Total is the point: every valid JSON
/// value has a case, so decoding an event payload cannot fail on a shape
/// nobody anticipated.
enum JSONValue: Decodable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
        } else if let v = try? c.decode(Bool.self) {
            self = .bool(v)
        } else if let v = try? c.decode(Double.self) {
            self = .number(v)
        } else if let v = try? c.decode(String.self) {
            self = .string(v)
        } else if let v = try? c.decode([String: JSONValue].self) {
            self = .object(v)
        } else if let v = try? c.decode([JSONValue].self) {
            self = .array(v)
        } else {
            self = .null
        }
    }

    /// Scalars render as themselves; containers deliberately do NOT get a
    /// synthesized description — dumping `{...}` into a person-facing timeline
    /// is plumbing, not information.
    var stringValue: String? {
        switch self {
        case .string(let s): return s
        case .number(let n): return n == n.rounded() ? String(Int(n)) : String(n)
        case .bool(let b): return b ? "true" : "false"
        case .object, .array, .null: return nil
        }
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let o) = self { return o }
        return nil
    }
}

/// A trace summary as returned by `GET /agent-traces` — the list route used
/// to find which trace to stream for an agent.
struct TraceSummary: Decodable, Identifiable {
    let id: String
    let threadId: String?
    let surface: String?
    let outcome: String?
    let startedAt: String?
    let finishedAt: String?
    let rootAgentId: String?

    private enum CodingKeys: String, CodingKey {
        case id, surface, outcome
        case threadId = "thread_id"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case rootAgentId = "root_agent_id"
    }

    var isRunning: Bool {
        (finishedAt ?? "").trimmingCharacters(in: .whitespaces).isEmpty
    }
}

struct TraceListResponse: Decodable {
    let items: [TraceSummary]
    let count: Int?
}

// MARK: - Display mapping

/// The vocabulary of steps the web app's WorkTab.tsx renders, ported so the
/// two products describe the same run in the same words. An event type this
/// does not recognise renders NOTHING — a timeline is not a log dump.
struct TraceStep: Identifiable {
    enum Tone {
        case normal, muted, success, warning, danger
    }

    let id: String
    let ts: String?
    let title: String
    let detail: String?
    let symbol: String
    let tone: Tone
    /// The key a later event resolves this step by (a tool result completing
    /// its own tool call). nil means the step stands alone.
    let resolutionKey: String?

    static func from(_ event: TraceEvent) -> TraceStep? {
        let type = event.normalizedType

        switch type {
        case "plan.started", "plan.item.created":
            return TraceStep(
                id: event.id, ts: event.ts,
                title: "Planned: \(event.string("title") ?? "next step")",
                detail: nil, symbol: "list.bullet", tone: .muted, resolutionKey: nil
            )

        case "tool.started":
            let key = "tool:\(event.toolCallId ?? event.id)"
            return TraceStep(
                id: key, ts: event.ts,
                title: toolLabel(event.string("tool_name")),
                detail: previewText(event.nested("args_preview")),
                symbol: toolSymbol(event.string("tool_name")),
                tone: .normal, resolutionKey: key
            )

        case "search.query":
            let key = "tool:\(event.toolCallId ?? event.id)"
            return TraceStep(
                id: key, ts: event.ts, title: "Searching the web",
                detail: event.string("query"), symbol: "magnifyingglass",
                tone: .normal, resolutionKey: key
            )

        case "subagent.invoked", "skill.invoked":
            let isSkill = type == "skill.invoked"
            let key = "tool:\(event.toolCallId ?? event.id)"
            let preview = event.nested("args_preview") ?? [:]
            let name = preview["skill"]?.stringValue
                ?? preview["name"]?.stringValue
                ?? preview["subagent_type"]?.stringValue
            return TraceStep(
                id: key, ts: event.ts,
                title: isSkill ? "Running skill" : "Delegating to a subagent",
                detail: name, symbol: isSkill ? "wrench.and.screwdriver" : "person.2",
                tone: .normal, resolutionKey: key
            )

        case "browser.action":
            return TraceStep(
                id: event.id, ts: event.ts,
                title: "Browser: \(event.string("action") ?? "action")",
                detail: event.string("url") ?? event.string("target_summary"),
                symbol: "globe", tone: .normal, resolutionKey: nil
            )

        case "delegation.started":
            let key = "deleg:\(event.childRunId ?? event.id)"
            let who = event.string("specialist_name") ?? event.string("specialist_id") ?? "a specialist"
            return TraceStep(
                id: key, ts: event.ts, title: "Delegated to \(who)",
                detail: nil, symbol: "person.2", tone: .normal, resolutionKey: key
            )

        case "approval.requested":
            let key = "appr:\(event.approvalId ?? event.id)"
            return TraceStep(
                id: key, ts: event.ts,
                title: "Approval requested: \(event.string("title") ?? "an action")",
                detail: nil, symbol: "exclamationmark.shield", tone: .warning, resolutionKey: key
            )

        case "artifact.created":
            let kind = event.string("kind") ?? "an artifact"
            let title = event.string("title")
            return TraceStep(
                id: event.id, ts: event.ts,
                title: title.map { "Created \(kind): \($0)" } ?? "Created \(kind)",
                detail: nil, symbol: "doc.text", tone: .muted, resolutionKey: nil
            )

        case "trace.failed":
            return TraceStep(
                id: event.id, ts: event.ts, title: "Ran into an error",
                detail: event.string("message"), symbol: "exclamationmark.triangle",
                tone: .danger, resolutionKey: nil
            )

        default:
            // trace.started/routed, plan churn, tool.progress and anything
            // unrecognised: not user-facing activity.
            return nil
        }
    }

    /// The key this event RESOLVES, if it is a completion of an earlier step.
    static func resolutionTarget(_ event: TraceEvent) -> String? {
        switch event.normalizedType {
        case "tool.result":
            return event.toolCallId.map { "tool:\($0)" }
        case "subagent.invoked", "skill.invoked":
            guard event.string("phase") == "result" else { return nil }
            return event.toolCallId.map { "tool:\($0)" }
        case "delegation.finished":
            return event.childRunId.map { "deleg:\($0)" }
        case "approval.resolved":
            return event.approvalId.map { "appr:\($0)" }
        default:
            return nil
        }
    }

    /// Applies a completion event onto the step it resolves.
    func resolved(by event: TraceEvent) -> TraceStep {
        let status = (event.string("status") ?? "").lowercased()
        let failed = status == "failed" || status == "error"

        switch event.normalizedType {
        case "approval.resolved":
            let decision = event.string("decision") ?? "resolved"
            let approved = decision.lowercased() == "approved"
            let actor = event.string("actor")
            return TraceStep(
                id: id, ts: ts, title: title,
                detail: actor.map { "\(decision) by \($0)" } ?? decision,
                symbol: approved ? "checkmark.shield" : "exclamationmark.shield",
                tone: approved ? .success : .danger, resolutionKey: nil
            )
        case "delegation.finished":
            return TraceStep(
                id: id, ts: ts, title: title,
                detail: event.string("result_summary") ?? detail,
                symbol: symbol, tone: failed ? .danger : .muted, resolutionKey: nil
            )
        default:
            return TraceStep(
                id: id, ts: ts, title: title,
                detail: event.string("summary") ?? detail,
                symbol: symbol, tone: failed ? .danger : .success, resolutionKey: nil
            )
        }
    }

    private static func toolLabel(_ name: String?) -> String {
        guard let name, !name.isEmpty else { return "Used a tool" }
        if name.hasPrefix("shell") { return "Ran a command" }
        if name.hasPrefix("document__") { return "Worked on a document" }
        if name.hasPrefix("project_task__") { return "Worked on a task" }
        if name.hasPrefix("browser") || name.hasPrefix("computer") { return "Used the browser" }
        if name.contains("search") { return "Searched" }
        if name.hasPrefix("memory") { return "Used its memory" }
        return name
    }

    private static func toolSymbol(_ name: String?) -> String {
        guard let name, !name.isEmpty else { return "wrench.and.screwdriver" }
        if name.hasPrefix("shell") { return "terminal" }
        if name.hasPrefix("document__") { return "doc.text" }
        if name.hasPrefix("project_task__") { return "checklist" }
        if name.hasPrefix("browser") || name.hasPrefix("computer") { return "globe" }
        if name.contains("search") { return "magnifyingglass" }
        if name.hasPrefix("memory") { return "brain" }
        return "wrench.and.screwdriver"
    }

    /// A short human line out of a tool's argument preview, or nil. Only
    /// scalar values are ever shown — a nested object is plumbing.
    private static func previewText(_ preview: [String: JSONValue]?) -> String? {
        guard let preview else { return nil }
        for key in ["command", "query", "path", "title", "url", "name", "text"] {
            if let value = preview[key]?.stringValue,
               !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return String(value.prefix(160))
            }
        }
        return nil
    }
}
