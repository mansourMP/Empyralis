import Foundation

/// Mirrors fleet_tools.fleet_list_agents's row shape. Deliberately narrow —
/// only what the two agent screens render. Full status parity with the web
/// app's deriveAgentStatus() (gateway cross-reference, CLI runtime state)
/// is NOT ported here — it's real complexity that needs the gateway list
/// too. What's shown is the honest subset: stopped / working (has a live
/// run) / ready. Never claim more precision than the data backs.
struct Agent: Codable, Identifiable, Equatable {
    let id: String
    let label: String?
    let agentKind: String?
    let channel: String?
    let currentRunId: String?
    let stopped: StoppedState?

    /// `id` COMES FROM `agent_id`, AND THERE IS NO `id` KEY TO FALL BACK TO.
    ///
    /// `fleet_list_agents` emits `agent_id` and nothing else — verified
    /// against the live route, not assumed. Decoding `id` from `"id"` threw
    /// `keyNotFound` on every row, which took the whole `AgentsResponse`
    /// down; `WorkspaceStore.refresh` wraps that fetch in `try?`, so the
    /// throw became an empty list and the Agents tab rendered "No agents
    /// yet" for a workspace that had agents. Nothing anywhere said why.
    ///
    /// Do not "fix" this back to `case id` because every sibling model uses
    /// `id` — the agent row is genuinely the odd one out.
    enum CodingKeys: String, CodingKey {
        case id = "agent_id"
        case label, channel
        case agentKind = "agent_kind"
        case currentRunId = "current_run_id"
        case stopped
    }

    var displayName: String { label?.isEmpty == false ? label! : id }

    var isWorking: Bool { currentRunId?.isEmpty == false }
    var isStopped: Bool { stopped?.active == true }

    var statusLabel: String {
        if isStopped { return "Stopped" }
        if isWorking { return "Working" }
        return "Ready"
    }
}

struct StoppedState: Codable, Equatable {
    let active: Bool?
}

struct AgentsResponse: Decodable {
    let ok: Bool
    let agents: [Agent]
}

/// Mirrors fleet_tools.fleet_get_agent_activity's event row shape exactly.
struct AgentActivityEvent: Decodable, Identifiable {
    let eventId: String
    let action: String
    let eventClass: String
    let title: String
    let status: String
    let channel: String?
    let createdAt: String

    var id: String { eventId }

    enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case action
        case eventClass = "event_class"
        case title, status, channel
        case createdAt = "created_at"
    }
}

struct AgentActivityResponse: Decodable {
    let ok: Bool
    let events: [AgentActivityEvent]
}
