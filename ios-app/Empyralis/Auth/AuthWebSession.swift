import AuthenticationServices
import CryptoKit
import Foundation
import Security
import UIKit

// MARK: - Why this file exists
//
// Two things in this app run an `ASWebAuthenticationSession`: `GoogleSignIn`
// (Google's own OAuth) and `NativeWebLogin` (the founder's "just opens
// Safari on the real website" flow). Both need the identical PKCE math
// (RFC 7636) and the identical answer to "which window presents the sheet."
// This used to live only inside `GoogleSignIn` as private helpers; pulled out
// here so the second flow reuses it instead of a second hand-copied
// implementation of the same three functions.

/// PKCE primitives, RFC 7636. Pure and stateless — every caller supplies its
/// own verifier/challenge/state, this just does the math.
enum PKCE {
    static func randomURLSafeToken(byteCount: Int = 64) -> String {
        var bytes = [UInt8](repeating: 0, count: byteCount)
        _ = SecRandomCopyBytes(kSecRandomDefault, byteCount, &bytes)
        return base64URL(Data(bytes))
    }

    static func s256(_ verifier: String) -> String {
        base64URL(Data(SHA256.hash(data: Data(verifier.utf8))))
    }

    /// RFC 7636 wants base64**url** with the padding removed. Plain base64
    /// contains `+` and `/`, which a query string re-encodes — the challenge
    /// then no longer matches the verifier and every exchange fails with
    /// `invalid_grant` (Google) or a generic refusal (our own
    /// `/auth/native/exchange`).
    static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// Shared `ASWebAuthenticationPresentationContextProviding` — every
/// `ASWebAuthenticationSession` in this app anchors to the same window, so
/// there is one answer to "which window presents the sheet" rather than one
/// per flow. A singleton rather than a per-flow instance: the session holds
/// this as a `weak var`, so a locally-scoped provider would need to be kept
/// alive by the caller for the life of the sheet — a long-lived singleton
/// sidesteps that entirely.
final class AuthPresentationContextProvider: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let shared = AuthPresentationContextProvider()

    private override init() {}

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first { $0.isKeyWindow }
            ?? ASPresentationAnchor()
    }
}
