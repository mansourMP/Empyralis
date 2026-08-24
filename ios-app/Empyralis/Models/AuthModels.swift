import Foundation

/// Mirrors server_modules/auth.py's `_auth_payload_for_user` — field names
/// are load-bearing, not decorative. Keep this in sync with the backend,
/// never guess a shape.
struct AuthPayload: Decodable {
    let ok: Bool
    let user: AuthUser
    let token: String?
    let currentWorkspaceId: String?
    let sessionRecovery: SessionRecovery?

    enum CodingKeys: String, CodingKey {
        case ok, user, token
        case currentWorkspaceId = "current_workspace_id"
        case sessionRecovery = "session_recovery"
    }
}

struct AuthUser: Decodable {
    let id: String
    let email: String?
    let name: String?
}

struct SessionRecovery: Decodable {
    let refreshToken: String

    enum CodingKeys: String, CodingKey {
        case refreshToken = "refresh_token"
    }
}
