import Foundation

// MARK: - Workspace members / invites — Settings ▸ Workspace
//
// Backend contract, VERIFIED LIVE against the seeded disposable backend
// (2026-08-25), server_modules/routes_workspaces.py, mirroring
// frontend/lib/workspace/fleet/members-data.ts exactly (same routes, same
// field names, same four-state email-delivery vocabulary):
//
//   GET  /workspaces/{id}/members   -> {"items": [WorkspaceMember]}
//        (already modeled — see TaskModels.swift's WorkspaceMember/
//        MembersResponse, read here from WorkspaceStore, no new fetch)
//   GET  /workspaces/{id}/invites   -> {"items": [WorkspacePendingInvite]}
//   POST /workspaces/{id}/invites   body {email, role}
//        -> {"invite": {...}, "token": ..., "email_delivery": {...}}
//
// Sent with no `project_id` — matches MembersSection.tsx's own Settings-page
// call (workspace access only), never ProjectMemberAdd.tsx's project-scoped
// variant.

/// `created_at` on an invite is epoch SECONDS (a bare JSON number —
/// Python's `int(time.time())`), unlike every other timestamp in this app,
/// which arrives as a string. Confirmed live:
/// `curl .../invites` -> `"created_at": 1787665601`. A naive
/// `Date(timeIntervalSince1970:)` applied to a STRING timestamp, or a plain
/// string decode applied to this NUMBER, would throw — this is the same
/// guard members-data.ts's own `inviteCreatedAtDate` exists for (its
/// comment: "a naive `new Date(seconds)` misreads seconds as
/// milliseconds-since-epoch and prints a Jan-1970 date").
enum InviteTimestamp: Decodable, Equatable {
    case epochSeconds(Double)
    case text(String)

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let n = try? c.decode(Double.self) {
            self = .epochSeconds(n)
            return
        }
        self = .text(try c.decode(String.self))
    }

    /// `TaskDates.parse` already carries the fractional-seconds/space-
    /// separator cascade this backend's STRING timestamps need — reused
    /// here for the `.text` branch rather than growing a second parser.
    var date: Date? {
        switch self {
        case .epochSeconds(let seconds): return Date(timeIntervalSince1970: seconds)
        case .text(let value): return TaskDates.parse(value)
        }
    }
}

struct WorkspacePendingInvite: Decodable, Identifiable, Equatable {
    let id: String
    let workspaceId: String
    let email: String
    let role: String
    let status: String
    let invitedByUserId: String?
    let createdAt: InviteTimestamp?

    enum CodingKeys: String, CodingKey {
        case id, email, role, status
        case workspaceId = "workspace_id"
        case invitedByUserId = "invited_by_user_id"
        case createdAt = "created_at"
    }
}

struct WorkspaceInvitesResponse: Decodable {
    let items: [WorkspacePendingInvite]
}

struct EmailDelivery: Decodable, Equatable {
    let status: String
    let email: String
}

struct CreatedInvite: Decodable, Equatable {
    let id: String
    let email: String
    let role: String

    enum CodingKeys: String, CodingKey {
        case id, email, role
    }
}

struct CreateInviteResponse: Decodable {
    let invite: CreatedInvite
    let token: String
    let emailDelivery: EmailDelivery?

    enum CodingKeys: String, CodingKey {
        case invite, token
        case emailDelivery = "email_delivery"
    }
}

/// Ports members-data.ts's `inviteDeliveryHint` verbatim — FOUR states,
/// never collapsed into sent/failed. This is CLAUDE.md's outcome-honesty
/// law at the one screen that mints a real, credential-bearing link: an
/// owner who is told "sent" when nothing went out will never look for the
/// copy-link fallback sitting right below it. `withheld_unverified_sender`
/// is not a failure — the invite and its link are real; the backend simply
/// declined to email it under the platform's name until the inviter's own
/// address is verified. Confirmed live: this seeded account is unverified,
/// so a real invite POST against it answers exactly this status.
func inviteDeliveryHint(_ delivery: EmailDelivery?) -> String {
    let email = delivery?.email ?? ""
    switch delivery?.status {
    case "sent":
        return email.isEmpty ? "Invite sent." : "Invite sent to \(email)."
    case "not_configured":
        return "Email isn't set up here — share this link instead."
    case "withheld_unverified_sender":
        return "Verify your own email to send invites by email — share this link for now."
    default:
        // Covers "failed" and anything unrecognized. The dangerous default
        // is the other one — claiming a send that never happened — so an
        // unknown status reads as the fallback, exactly like the web.
        return "The invite email didn't send — share this link instead."
    }
}

/// "Owner" only — matches `create_workspace_invite_route`'s server-side
/// `minimum_role="owner"` gate. Computed from `WorkspaceStore.members`
/// (already loaded, local-first) rather than a new fetch: the signed-in
/// user's own row carries the SAME `role` field this function reads,
/// straight from `list_workspace_members_route`.
func canInviteToWorkspace(members: [WorkspaceMember], ownUserId: String?) -> Bool {
    guard let ownUserId, !ownUserId.isEmpty else { return false }
    return members.first { $0.userId == ownUserId }?.role == "owner"
}

func workspaceRoleLabel(_ role: String?) -> String {
    switch role {
    case "owner": return "Owner"
    case "viewer": return "Viewer"
    default: return "Member"
    }
}

// MARK: - Hardware — Settings ▸ Connections
//
// `GET /gateway/registrations?workspace_id=…` -> always
// `{"workspace_id":…, "count": N, "items": [...]}` — verified against
// `gateway_registry_service.list_workspace_gateways` (file:line 558-571,
// returns that exact three-key shape unconditionally) AND live, against
// the seeded workspace (empty today: `{"workspace_id":"ws_…","count":0,
// "items":[]}`). Per-item fields below are the ones this read-only screen
// renders, verified against `gateway_registration_public_payload`
// (gateway_registry_service.py:330-431) and `_gateway_connection_payload`
// / `_hardware_presentation` in the same file — narrower than the web's
// full `FleetGateway`, which also carries live resource metrics, LLM
// runtime detection and self-update state this pass deliberately does not
// surface (see the file header on ConnectionsSettingsView for why).
struct GatewayRegistration: Decodable, Identifiable, Equatable {
    let gatewayId: String
    let displayName: String?
    let platform: String?
    let hardwareKind: String?
    let hardwareLabel: String?
    let hardwareProvider: String?
    let connectionStatus: String?
    let status: String?
    let lastSeenAt: String?
    let gatewayVersion: String?

    var id: String { gatewayId }

    enum CodingKeys: String, CodingKey {
        case gatewayId = "gateway_id"
        case displayName = "display_name"
        case platform
        case hardwareKind = "hardware_kind"
        case hardwareLabel = "hardware_label"
        case hardwareProvider = "hardware_provider"
        case connectionStatus = "connection_status"
        case status
        case lastSeenAt = "last_seen_at"
        case gatewayVersion = "gateway_version"
    }
}

struct GatewayRegistrationsResponse: Decodable {
    let items: [GatewayRegistration]
}

/// A box's live reachability, reduced to one tone + one label — ported 1:1
/// from `connectionPresentation` (frontend/lib/workspace/fleet/gateway-box-
/// picker.tsx), which is itself "the one place a box's live reachability
/// becomes a StatusChip tone+label" on the web, specifically so the same
/// box never reads differently on two screens. Kept a pure function over a
/// plain string (never a SwiftUI `Color`) so it can be unit-tested the same
/// way, and so a caller decides how `.degraded`/`.offline`/etc. map to
/// color in ITS OWN theme context rather than this file importing SwiftUI.
enum ConnectionTone: String {
    case online, degraded, offline, error
}

func gatewayConnectionPresentation(_ raw: String?) -> (tone: ConnectionTone, label: String) {
    switch (raw ?? "").lowercased() {
    case "online": return (.online, "Online")
    // Connectivity and execution readiness are different facts (the web's
    // own comment: a box can have a live session while its Docker sandbox
    // isn't ready) — never folded into a plain "Online".
    case "execution_blocked": return (.degraded, "Online — tools unavailable")
    case "degraded": return (.degraded, "Degraded")
    case "reconnecting": return (.degraded, "Reconnecting")
    case "revoked": return (.error, "Revoked")
    default: return (.offline, "Offline")
    }
}

/// "Cloud · DigitalOcean" / "Local computer" / "Computer" — a narrower port
/// of HardwareSection.tsx's `typeLabel` (drops the region/country-flag
/// detail, which this read-only screen doesn't fetch).
func hardwareTypeLine(_ g: GatewayRegistration) -> String {
    if g.hardwareKind == "cloud_vps" {
        let provider = g.hardwareProvider?.trimmingCharacters(in: .whitespaces)
        let label = (provider?.isEmpty == false ? provider!.capitalized : nil) ?? "Cloud"
        return "Cloud · \(label)"
    }
    let isLocalMac = (g.platform ?? "").lowercased().hasPrefix("darwin")
    return isLocalMac ? "Local computer" : "Computer"
}

// MARK: - MCP servers — Settings ▸ Connections
//
// `GET /agent-registry/mcp/servers?workspace_id=…` -> always
// `{"advanced_only": true, "items": [...]}` — verified live against the
// seeded backend both empty AND populated (this pass PUT one real test row
// via `PUT /agent-registry/mcp/servers/{id}` to capture the shape; see
// SettingsModelsTests). Field list mirrors McpServersSection.tsx's
// `McpServerRecord`, narrowed to what a read-only summary shows — the
// per-tool approval bucketing (autoActive/needsApproval/approvedHighStakes/
// turnedOff) is a WRITE surface and out of scope here (see this file's own
// header comment for why).
struct McpServerRecord: Decodable, Identifiable, Equatable {
    let id: String
    let label: String
    let endpoint: String
    let enabled: Bool
    let toolCount: Int
    let status: String?
    let lastSyncedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, label, endpoint, enabled, status
        case toolCount = "tool_count"
        case lastSyncedAt = "last_synced_at"
    }
}

struct McpServersResponse: Decodable {
    let items: [McpServerRecord]
}

/// A coarser, read-only port of McpServersSection.tsx's `groupStatusText` —
/// drops the per-tool "N actions need your approval" count (that needs the
/// full `tools[]` array, a write-surface concern this screen doesn't fetch)
/// but keeps every state that is honest without it.
func mcpServerStatusLabel(_ server: McpServerRecord) -> String {
    if server.status == "reauth_required" { return "Reconnect needed — sign-in expired" }
    if !server.enabled { return "Disabled" }
    if server.toolCount == 0 {
        return server.lastSyncedAt != nil ? "No tools discovered" : "Pending discovery"
    }
    return "Connected — \(server.toolCount) \(server.toolCount == 1 ? "tool" : "tools")"
}

func mcpServerHost(_ endpoint: String) -> String {
    URL(string: endpoint)?.host ?? endpoint
}
