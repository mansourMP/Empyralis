import Foundation

@MainActor
final class SessionStore: ObservableObject {
    enum State {
        case unknown
        case signedOut
        case signedIn
    }

    @Published private(set) var state: State = .unknown
    @Published private(set) var currentWorkspaceId: String?
    @Published private(set) var user: AuthUser?
    @Published var lastErrorMessage: String?

    private let refreshTokenKey = "refresh_token"
    private let deviceIdKey = "device_id"

    private var deviceId: String {
        if let existing = Keychain.get(deviceIdKey) { return existing }
        let fresh = UUID().uuidString
        Keychain.set(fresh, forKey: deviceIdKey)
        return fresh
    }

    func restoreSession() async {
        guard let refreshToken = Keychain.get(refreshTokenKey), !refreshToken.isEmpty else {
            state = .signedOut
            return
        }
        do {
            try await refresh(using: refreshToken)
        } catch {
            Keychain.remove(refreshTokenKey)
            state = .signedOut
        }
    }

    func login(email: String, password: String) async {
        lastErrorMessage = nil
        do {
            let payload: AuthPayload = try await APIClient.shared.post("/auth/login", body: [
                "email": email,
                "password": password,
                "channel": "mobile",
                "device_id": deviceId,
                "device_name": APIConfig.deviceName,
                "device_platform": APIConfig.devicePlatform,
            ])
            apply(payload)
        } catch APIError.server(let message) {
            lastErrorMessage = message
        } catch APIError.unauthorized {
            // "WRONG PASSWORD" AND "COULDN'T REACH THE SERVER" ARE DIFFERENT
            // FACTS AND MUST NOT SHARE ONE MESSAGE. Without this branch a 401
            // fell into the generic catch below and the login screen said
            // "Check your connection" about a connection that was working
            // perfectly — sending someone to their wifi settings when the
            // thing to fix was their password. Verified live: a rejected
            // password and an unreachable backend produced byte-identical
            // copy.
            //
            // Deliberately does not say WHICH of the two was wrong, matching
            // the server's own "Invalid email or password." — naming the
            // wrong half tells an attacker which addresses exist.
            lastErrorMessage = "That email and password don't match."
        } catch {
            lastErrorMessage = "Couldn't sign in. Check your connection and try again."
        }
    }

    func signOut() {
        Keychain.remove(refreshTokenKey)
        APIClient.shared.bearerToken = nil
        currentWorkspaceId = nil
        user = nil
        state = .signedOut
    }

    /// Called by anything that gets a 401 mid-session — refreshes once,
    /// signs out on failure. Never loops.
    func handleUnauthorized() async {
        guard let refreshToken = Keychain.get(refreshTokenKey) else {
            signOut()
            return
        }
        do {
            try await refresh(using: refreshToken)
        } catch {
            signOut()
        }
    }

    private func refresh(using refreshToken: String) async throws {
        let payload: AuthPayload = try await APIClient.shared.post("/auth/refresh", body: [
            "refresh_token": refreshToken,
            "channel": "mobile",
            "device_id": deviceId,
            "device_name": APIConfig.deviceName,
            "device_platform": APIConfig.devicePlatform,
        ])
        apply(payload)
    }

    private func apply(_ payload: AuthPayload) {
        APIClient.shared.bearerToken = payload.token
        currentWorkspaceId = payload.currentWorkspaceId
        user = payload.user
        if let newRefreshToken = payload.sessionRecovery?.refreshToken {
            Keychain.set(newRefreshToken, forKey: refreshTokenKey)
        }
        state = payload.token != nil ? .signedIn : .signedOut
    }
}
