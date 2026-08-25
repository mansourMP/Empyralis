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

    /// Whether the login screen may offer "Continue with Google".
    ///
    /// TWO facts have to agree, and both are real:
    ///
    ///   1. this BUILD carries a Google iOS OAuth client id
    ///      (`GoogleAuthConfig.isConfigured` — local, synchronous, decisive:
    ///       without it there is literally nothing to send to Google)
    ///   2. this BACKEND accepts Google identities
    ///      (`GET /auth/providers` -> `google.enabled`, the SAME gate the web
    ///       login uses at `frontend/app/login/page.tsx`)
    ///
    /// Starts as fact 1 alone so a configured build shows the button on the
    /// first frame rather than popping it in — the local-first rule applies
    /// to the entry screen too. Fact 2 can only ever REMOVE it.
    @Published private(set) var googleSignInAvailable = GoogleAuthConfig.isConfigured

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

    /// Confirms fact 2 above. Never turns the button ON — a build with no
    /// client id stays dark no matter what the server says — and a failure to
    /// ask leaves the current answer alone, because "the providers call
    /// didn't come back" is not "Google is off".
    func refreshGoogleAvailability() async {
        guard GoogleAuthConfig.isConfigured else { return }
        guard let providers: AuthProviders = try? await APIClient.shared.get("/auth/providers") else { return }
        googleSignInAvailable = providers.google?.enabled ?? false
    }

    func loginWithGoogle() async {
        lastErrorMessage = nil
        let identityToken: String
        do {
            identityToken = try await GoogleSignIn().identityToken()
        } catch GoogleSignInError.cancelled {
            // Backing out of the sheet is a decision, not a failure. Saying
            // anything here would put a red line on screen for someone who
            // simply changed their mind.
            return
        } catch GoogleSignInError.network {
            lastErrorMessage = "Couldn't reach Google. Check your connection and try again."
            return
        } catch GoogleSignInError.googleRefused(let code) {
            // Google's own error code is kept when it sent one: `invalid_client`
            // and `access_denied` send whoever reads this to completely
            // different places, and dropping it would leave one sentence for
            // both.
            lastErrorMessage = code.map { "Google didn't complete the sign-in (\($0))." }
                ?? "Google didn't complete the sign-in."
            return
        } catch GoogleSignInError.notConfigured {
            lastErrorMessage = "Google sign-in isn't set up in this build."
            return
        } catch {
            lastErrorMessage = "Google sign-in didn't finish. Try again."
            return
        }

        do {
            apply(try await exchangeIdentityToken(identityToken, provider: "google"))
        } catch APIError.server(let message) {
            // OUR SERVER REFUSED, and it said why. The likeliest 401 here is
            // "Identity token audience is invalid." — which means this app's
            // client id is not in the backend's allow-list, i.e. a config
            // problem on our side and NOT a problem with the person's Google
            // account. Relaying the server's own sentence is the only way
            // that distinction survives to the screen.
            lastErrorMessage = message
        } catch {
            lastErrorMessage = "Couldn't finish signing in. Check your connection and try again."
        }
    }

    /// `POST /auth/provider-login`, deliberately NOT through
    /// `APIClient.post`.
    ///
    /// `APIClient` maps every 401 to `.unauthorized` and DISCARDS the body —
    /// correct for the rest of the app, where a 401 means "refresh or sign
    /// out" and the prose is noise. On this one route a 401 is the normal
    /// way a misconfiguration reports itself and the `detail` is the entire
    /// diagnosis, so throwing it away would leave the screen saying
    /// something generic about a problem that names itself precisely. Scoped
    /// to this route rather than changing `APIClient`'s contract for
    /// everyone.
    private func exchangeIdentityToken(_ identityToken: String, provider: String) async throws -> AuthPayload {
        var request = URLRequest(url: APIConfig.baseURL.appendingPathComponent("/auth/provider-login"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "provider": provider,
            "identity_token": identityToken,
            // MANDATORY. Omit it and `browser_auth_session_channel` defaults
            // to "web", the server sets HttpOnly cookies instead, and
            // `_sanitize_browser_auth_payload` strips `token` and
            // `session_recovery` from the body — a 200 that signs nobody in.
            "channel": "mobile",
            "device_id": deviceId,
            "device_name": APIConfig.deviceName,
            "device_platform": APIConfig.devicePlatform,
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.server("No response") }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode(ProviderLoginErrorBody.self, from: data))?.detail
            throw APIError.server(detail ?? "Sign-in was refused (\(http.statusCode)).")
        }
        return try JSONDecoder().decode(AuthPayload.self, from: data)
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

/// `GET /auth/providers` — mirrors `auth.py`'s `available_auth_providers`.
/// Every field optional: a backend that predates a provider omits its key
/// entirely, and a missing key must read as "off", never as a decode failure
/// that hides the whole answer.
struct AuthProviders: Decodable {
    struct Provider: Decodable { let enabled: Bool? }
    let google: Provider?
    let apple: Provider?
    let email: Provider?
}

private struct ProviderLoginErrorBody: Decodable {
    let detail: String?
}
