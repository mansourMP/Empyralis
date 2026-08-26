import AuthenticationServices
import Foundation

// MARK: - Why this is a hand-rolled OAuth client and not an SDK
//
// This project has ZERO SPM dependencies and keeps it that way, so
// GoogleSignIn-iOS is not on the table. Nothing here needs it: a native
// OAuth client is a URL, a sheet, and one form POST, and Apple ships the
// sheet (`ASWebAuthenticationSession`) and the hash (`CryptoKit`).
//
// THE APP DOES NOT REUSE THE WEB'S OWN GOOGLE FLOW, and that is not a
// preference — it structurally cannot. `frontend/app/api/auth/google/*`
// finishes on an HTTPS page that sets HttpOnly cookies and hardcodes
// `channel: 'web'`, which makes the backend STRIP `token` from the body
// (`_sanitize_browser_auth_payload`). So driving it from a web-auth session
// would end with: no custom-scheme callback for the session to catch, a
// token deliberately removed from the response, and credentials parked in
// cookies the session cannot read. Three independent dead ends.
//
// What this app does instead is the standard native pattern, and it lands on
// a backend route that was already built for it:
//
//     ASWebAuthenticationSession + PKCE  ──▶  Google
//                                              │  authorization code
//                                              ▼
//                       this app exchanges it itself (no client secret —
//                       an iOS OAuth client is a PUBLIC client, PKCE is
//                       what replaces the secret)
//                                              │  id_token (RS256 JWT)
//                                              ▼
//        POST /auth/provider-login  { provider: "google", identity_token,
//                                     channel: "mobile" }
//                                              │
//                                              ▼
//                       a real bearer token + a 180-day refresh token
//
// `auth.py`'s `verify_external_identity_token` verifies that JWT against
// Google's live JWKS — it is real verification, not a decode — so the app
// handing over an ID token is not the app asserting who the user is.

/// Where the iOS OAuth client id comes from, and everything derived from it.
///
/// **A build with no client id renders no Google button at all** — see
/// `LoginView`. That is the whole "no dead controls" answer here: the one
/// fact that decides whether this flow can even start is known locally and
/// synchronously, so the control is absent rather than present-and-doomed.
enum GoogleAuthConfig {

    /// Set `EmpyralisGoogleClientID` in `project.yml`'s Info.plist block —
    /// NEVER by hand-editing `Empyralis/Info.plist`, which is GENERATED and
    /// whose edits the next `xcodegen generate` erases.
    ///
    /// The environment override mirrors `APIConfig.baseURL`'s: it is how you
    /// try a client id on a simulator without editing tracked files.
    static var clientId: String? {
        let fromEnv = ProcessInfo.processInfo.environment["EMPYRALIS_GOOGLE_IOS_CLIENT_ID"]
        let fromPlist = Bundle.main.object(forInfoDictionaryKey: "EmpyralisGoogleClientID") as? String
        let raw = (fromEnv ?? fromPlist ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        // xcodegen writes the key unconditionally, so an unconfigured build
        // carries the empty string rather than no key — treat both as absent.
        return raw.isEmpty ? nil : raw
    }

    /// Google's reverse-DNS callback scheme for an iOS client:
    /// `123-abc.apps.googleusercontent.com` -> `com.googleusercontent.apps.123-abc`.
    ///
    /// Returns nil for anything that is not an iOS-shaped client id. That
    /// refusal is deliberate: a WEB client id has no custom scheme Google
    /// will accept, so guessing one would produce a sheet that opens, shows
    /// `redirect_uri_mismatch`, and never calls back.
    static var callbackScheme: String? { callbackScheme(forClientId: clientId) }

    /// Pure, and separated from the property so it can be tested: getting
    /// this wrong is SILENT. A scheme that does not match what Google has
    /// registered produces a sheet that opens, shows `redirect_uri_mismatch`
    /// inside Google's own page, and never calls back — which looks like a
    /// hung sheet, not like a bad string.
    static func callbackScheme(forClientId clientId: String?) -> String? {
        guard let clientId else { return nil }
        let suffix = ".apps.googleusercontent.com"
        guard clientId.hasSuffix(suffix) else { return nil }
        let prefix = String(clientId.dropLast(suffix.count))
        guard !prefix.isEmpty else { return nil }
        return "com.googleusercontent.apps.\(prefix)"
    }

    static var redirectURI: String? { redirectURI(forClientId: clientId) }

    static func redirectURI(forClientId clientId: String?) -> String? {
        guard let scheme = callbackScheme(forClientId: clientId) else { return nil }
        return "\(scheme):/oauth2redirect"
    }

    /// True only when every piece the flow needs is present.
    static var isConfigured: Bool { redirectURI != nil }
}

/// Every way this flow can end badly, kept SEPARATE on purpose.
///
/// "You closed the sheet", "Google said no", "our server said no" and "the
/// network is down" are four different facts, and collapsing them is the
/// failure this codebase has already shipped once on this very screen (a
/// wrong password reporting "Check your connection"). Each case here maps to
/// its own sentence, and `cancelled` maps to NO sentence at all — backing
/// out of a sheet is a decision, not an error.
enum GoogleSignInError: Error {
    /// The person dismissed the sheet. Not a failure. Say nothing.
    case cancelled
    /// No client id in this build — should be unreachable, because the
    /// button does not render. Kept so it can never fail silently.
    case notConfigured
    /// Google itself refused: consent denied, bad client, bad redirect.
    /// Carries Google's own `error` code when it sent one.
    case googleRefused(String?)
    /// The sheet could not be presented at all.
    case couldNotPresent
    /// Google answered, but not with the thing we asked for.
    case malformedResponse
    /// Transport failure reaching Google.
    case network
}

/// Runs the sheet and returns a Google **ID token**.
///
/// Deliberately does NOT talk to Empyralis — handing the ID token to
/// `/auth/provider-login` is `SessionStore`'s job. Keeping the two apart is
/// what makes "Google refused" and "our server refused" impossible to
/// confuse at the call site.
@MainActor
final class GoogleSignIn: NSObject {

    /// The session must be held for as long as it is on screen; a local
    /// would be released the moment `start()` returns and the sheet would
    /// vanish mid-flow.
    private var session: ASWebAuthenticationSession?

    func identityToken() async throws -> String {
        guard
            let clientId = GoogleAuthConfig.clientId,
            let redirectURI = GoogleAuthConfig.redirectURI,
            let callbackScheme = GoogleAuthConfig.callbackScheme
        else {
            throw GoogleSignInError.notConfigured
        }

        // PKCE. An iOS OAuth client is PUBLIC — it ships inside an app
        // anyone can unzip, so it has no secret. The verifier is what proves
        // the app redeeming the code is the app that asked for it.
        // (PKCE is shared with NativeWebLogin — see AuthWebSession.swift.)
        let verifier = PKCE.randomURLSafeToken()
        let challenge = PKCE.s256(verifier)
        let state = PKCE.randomURLSafeToken(byteCount: 32)
        let nonce = PKCE.randomURLSafeToken(byteCount: 32)

        var authorize = URLComponents(string: "https://accounts.google.com/o/oauth2/v2/auth")!
        authorize.queryItems = [
            .init(name: "client_id", value: clientId),
            .init(name: "redirect_uri", value: redirectURI),
            .init(name: "response_type", value: "code"),
            .init(name: "scope", value: "openid email profile"),
            .init(name: "code_challenge", value: challenge),
            .init(name: "code_challenge_method", value: "S256"),
            .init(name: "state", value: state),
            .init(name: "nonce", value: nonce),
            // Matches the web flow's own parameter, so the same person sees
            // the same account chooser on both products.
            .init(name: "prompt", value: "select_account"),
        ]

        let callbackURL = try await present(authorize.url!, callbackScheme: callbackScheme)
        let code = try Self.authorizationCode(from: callbackURL, expectedState: state)
        return try await Self.exchange(
            code: code,
            verifier: verifier,
            clientId: clientId,
            redirectURI: redirectURI
        )
    }

    // MARK: - The sheet

    private func present(_ url: URL, callbackScheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: url,
                callbackURLScheme: callbackScheme
            ) { callbackURL, error in
                if let error {
                    // A DISMISSED SHEET IS NOT A FAILED SIGN-IN. This is the
                    // one branch that must not produce an error message.
                    let code = (error as? ASWebAuthenticationSessionError)?.code
                    continuation.resume(
                        throwing: code == .canceledLogin
                            ? GoogleSignInError.cancelled
                            : GoogleSignInError.googleRefused(nil)
                    )
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: GoogleSignInError.malformedResponse)
                    return
                }
                continuation.resume(returning: callbackURL)
            }
            // Shared across every ASWebAuthenticationSession in this app —
            // see AuthPresentationContextProvider's own comment.
            session.presentationContextProvider = AuthPresentationContextProvider.shared
            // FALSE, deliberately: sharing Safari's cookie jar is what makes
            // an already-signed-in Google account one tap instead of a
            // password. Ephemeral would re-ask for credentials every time,
            // which is the opposite of the "it just works" this exists for.
            session.prefersEphemeralWebBrowserSession = false
            self.session = session
            if !session.start() {
                continuation.resume(throwing: GoogleSignInError.couldNotPresent)
            }
        }
    }

    // MARK: - Callback

    private static func authorizationCode(from url: URL, expectedState: String) throws -> String {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let value = { (name: String) in items.first { $0.name == name }?.value }

        if let error = value("error") {
            throw GoogleSignInError.googleRefused(error)
        }
        // Checked even though the callback scheme already scopes the reply to
        // this app — an unverified state is a hole that costs one line to
        // close, and a mismatch means something other than our own request
        // produced this URL.
        guard value("state") == expectedState else {
            throw GoogleSignInError.malformedResponse
        }
        guard let code = value("code"), !code.isEmpty else {
            throw GoogleSignInError.malformedResponse
        }
        return code
    }

    // MARK: - Code -> ID token

    private static func exchange(
        code: String,
        verifier: String,
        clientId: String,
        redirectURI: String
    ) async throws -> String {
        var request = URLRequest(url: URL(string: "https://oauth2.googleapis.com/token")!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")

        var form = URLComponents()
        form.queryItems = [
            .init(name: "client_id", value: clientId),
            .init(name: "code", value: code),
            .init(name: "code_verifier", value: verifier),
            .init(name: "grant_type", value: "authorization_code"),
            .init(name: "redirect_uri", value: redirectURI),
        ]
        request.httpBody = form.percentEncodedQuery?.data(using: .utf8)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            // COULD NOT REACH GOOGLE is not GOOGLE SAID NO.
            throw GoogleSignInError.network
        }

        let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let body = parsed ?? nil else {
            throw GoogleSignInError.malformedResponse
        }
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            // Google's own OAuth error code (`invalid_grant`,
            // `invalid_client`, …) rather than the status number — the code
            // is the part that says which thing is misconfigured.
            throw GoogleSignInError.googleRefused(body["error"] as? String)
        }
        guard let idToken = body["id_token"] as? String, !idToken.isEmpty else {
            // A 200 with an access token and no id_token means the `openid`
            // scope did not survive — a real, silent way to get a "working"
            // response that this flow cannot use.
            throw GoogleSignInError.malformedResponse
        }
        return idToken
    }
}
