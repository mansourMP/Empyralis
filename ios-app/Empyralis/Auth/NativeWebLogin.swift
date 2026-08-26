import AuthenticationServices
import Foundation

// MARK: - The founder's own description of this flow
//
// "press the button and it just goes to open... a page that seems like a
// Safari because it's default probably" — and login happens there, however
// the website itself offers it (email/password, Google, whatever it grows
// later), with the app never seeing a password or handling a second copy of
// any login form. This is Linear's own pattern.
//
// The backend half is already built — see `server_modules/native_auth_
// service.py`'s own header comment for the full authorization-code + PKCE
// design (RFC 7636) this mirrors. In short:
//
//     app   ──▶  https://empyralis.ai/login?native=ios&code_challenge=<S256>
//                &state=<random>                              (Safari sheet)
//     ...person logs in on the website, however they choose...
//     web   ──▶  empyralis://auth?code=<single-use, 90s>&state=<echoed back>
//     app   ──▶  POST /auth/native/exchange {code, code_verifier}
//                <── the SAME payload a channel="mobile" login returns
//
// `state` is generated here, sent in the opening URL, and checked against
// what the callback echoes back — purely a CLIENT-side check (the exchange
// endpoint itself takes no `state` at all; the server-side half of this
// design is the code being single-use and bound to a code_challenge, not
// `state`). A mismatch means this callback may not be answering the request
// this app made, and is refused rather than trusted.

/// What `NativeWebLogin.run()` hands back on success: a single-use code plus
/// the verifier that proves this app — and not anyone who happened to
/// observe the code — asked for it. Deliberately does NOT talk to Empyralis;
/// exchanging it at `/auth/native/exchange` and storing the result is
/// `SessionStore`'s job, exactly the same split `GoogleSignIn.identityToken()`
/// keeps from `SessionStore.loginWithGoogle()` — so "the browser step didn't
/// finish" and "our server refused the code" can never be confused at the
/// call site.
struct NativeWebLoginResult {
    let code: String
    let codeVerifier: String
}

/// Every way this flow can end without a usable code, kept SEPARATE —
/// same discipline as `GoogleSignInError` on this exact screen.
enum NativeWebLoginError: Error {
    /// The person dismissed the sheet. Not a failure. Say nothing.
    ///
    /// NOTE on the state this deliberately does NOT model: a LOGIN FAILURE
    /// on the website itself (wrong password, etc.) never reaches here as a
    /// distinct case. The website shows its own error inline and the sheet
    /// stays open — the person either fixes it and the flow succeeds, or
    /// they give up and cancel, which lands in this same case. There is
    /// nothing for the app to say that the website hasn't already said.
    case cancelled
    /// The sheet could not be presented, or ended with some non-cancel
    /// system-level error (not a website login failure — see above).
    case sessionFailed
    /// The callback arrived but had no usable `code`/`state` in it.
    case malformedCallback
    /// The callback's `state` did not match what this app sent when it
    /// opened the sheet. Kept distinct from `malformedCallback` on purpose —
    /// one is a shape problem, the other is "this reply does not prove it
    /// is answering our own request," which is a security fact, not a
    /// parsing one.
    case stateMismatch
}

/// Runs the Safari sheet against the real website and returns a handoff
/// code. See this file's header for the full design.
@MainActor
final class NativeWebLogin {
    private var session: ASWebAuthenticationSession?

    /// Matches `native_auth_service.NATIVE_REDIRECT_TARGETS["ios"]` —
    /// `empyralis://auth`. The scheme alone is what `ASWebAuthenticationSession`
    /// needs; the redirect target's exact shape is the backend's to decide,
    /// never this app's (see that module's own header, point 2).
    private static let callbackScheme = "empyralis"

    func run() async throws -> NativeWebLoginResult {
        // PKCE — shared with GoogleSignIn, see AuthWebSession.swift.
        let verifier = PKCE.randomURLSafeToken()
        let challenge = PKCE.s256(verifier)
        let state = PKCE.randomURLSafeToken(byteCount: 32)

        var components = URLComponents(
            url: APIConfig.webOrigin.appendingPathComponent("login"),
            resolvingAgainstBaseURL: false
        )!
        components.queryItems = [
            URLQueryItem(name: "native", value: "ios"),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "state", value: state),
        ]

        let callbackURL = try await present(components.url!)
        let code = try Self.code(from: callbackURL, expectedState: state)
        return NativeWebLoginResult(code: code, codeVerifier: verifier)
    }

    // MARK: - The sheet

    private func present(_ url: URL) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: url,
                callbackURLScheme: Self.callbackScheme
            ) { callbackURL, error in
                if let error {
                    // A DISMISSED SHEET IS NOT A FAILED SIGN-IN — the one
                    // branch that must not produce an error message.
                    let code = (error as? ASWebAuthenticationSessionError)?.code
                    continuation.resume(
                        throwing: code == .canceledLogin
                            ? NativeWebLoginError.cancelled
                            : NativeWebLoginError.sessionFailed
                    )
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: NativeWebLoginError.malformedCallback)
                    return
                }
                continuation.resume(returning: callbackURL)
            }
            // Shared across every ASWebAuthenticationSession in this app —
            // see AuthPresentationContextProvider's own comment.
            session.presentationContextProvider = AuthPresentationContextProvider.shared
            // FALSE, deliberately, same reasoning as GoogleSignIn: sharing
            // Safari's cookie jar means someone already signed in to
            // empyralis.ai in Safari lands back in the app almost
            // immediately rather than being asked to authenticate twice.
            session.prefersEphemeralWebBrowserSession = false
            self.session = session
            if !session.start() {
                continuation.resume(throwing: NativeWebLoginError.sessionFailed)
            }
        }
    }

    // MARK: - Callback

    private static func code(from url: URL, expectedState: String) throws -> String {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let value = { (name: String) in items.first { $0.name == name }?.value }

        // Checked BEFORE `code`, deliberately: an absent state is a shape
        // problem (malformed), but a PRESENT, WRONG state is the security
        // fact — the two must not collapse into one branch, or a genuine
        // mismatch would just look like a parsing hiccup.
        guard let returnedState = value("state") else {
            throw NativeWebLoginError.malformedCallback
        }
        guard returnedState == expectedState else {
            throw NativeWebLoginError.stateMismatch
        }
        guard let code = value("code"), !code.isEmpty else {
            throw NativeWebLoginError.malformedCallback
        }
        return code
    }
}
