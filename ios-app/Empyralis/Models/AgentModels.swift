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

    /// The customer-facing name of the workspace assistant. It is a plain
    /// assistant inside the platform, not a persona, and this is the only
    /// name it has. Mirrors the web's `WORKSPACE_ASSISTANT_LABEL`
    /// (frontend/lib/workspace/fleet/fleet-presentation.ts).
    static let workspaceAssistantLabel = "Ask AI"

    /// What to PRINT for this agent. Never read `label` directly at a render
    /// site — this is the one place that decides.
    ///
    /// The master install's STORED label on every workspace created before
    /// 2026-08-28 is literally "Sage", the persona name the founder removed
    /// from the product. That column is data (rewriting it across live rows
    /// is a migration, not a rename), so the assistant prints as "Ask AI"
    /// here whatever the row says.
    ///
    /// This is not cosmetic on iOS and the phone leaked it worse than web
    /// did: TaskDetailView's "Created by"/"Completed by" rows and
    /// DocumentEditSheet's revision authorship both resolve against
    /// `store.agents` — the UNFILTERED list, unlike `store.realAgents` — so
    /// any task the assistant created printed the old name on the primary
    /// task screen. Web's own TaskDetailView never showed it, because it is
    /// handed a project-filtered list and the assistant carries no project.
    ///
    /// Keyed on `agentKind` first: that is the structural fact, and the
    /// label-substring clause below it is only a fallback for rows that
    /// predate the field.
    var displayName: String {
        let kind = (agentKind ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        if kind == "master" { return Self.workspaceAssistantLabel }
        guard let label, !label.isEmpty else { return id }
        if label.lowercased().contains("sage") { return Self.workspaceAssistantLabel }
        return label
    }

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
