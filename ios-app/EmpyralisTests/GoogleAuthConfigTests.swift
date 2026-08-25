import XCTest
@testable import Empyralis

/// The reverse-DNS callback scheme is the one piece of this flow that fails
/// SILENTLY when it is wrong — a mismatched scheme means Google renders
/// `redirect_uri_mismatch` inside its own sheet and never calls back, which
/// presents as a hung sheet rather than as a bad string. So it is a pure
/// function with a test, not an inline expression.
final class GoogleAuthConfigTests: XCTestCase {

    func testDerivesGooglesReversedClientIdScheme() {
        XCTAssertEqual(
            GoogleAuthConfig.callbackScheme(
                forClientId: "1234567890-abcdefg.apps.googleusercontent.com"
            ),
            "com.googleusercontent.apps.1234567890-abcdefg"
        )
    }

    func testRedirectUriUsesASingleSlashPath() {
        // `scheme:/path`, NOT `scheme://path`. The authorization request and
        // the token exchange must send byte-identical redirect_uri values or
        // Google answers the exchange with `invalid_grant` — and that error
        // arrives at the very end, after a successful-looking consent screen.
        XCTAssertEqual(
            GoogleAuthConfig.redirectURI(
                forClientId: "1234567890-abcdefg.apps.googleusercontent.com"
            ),
            "com.googleusercontent.apps.1234567890-abcdefg:/oauth2redirect"
        )
    }

    func testRefusesAClientIdThatIsNotGoogleShaped() {
        // A WEB client id has no custom scheme Google will accept. Returning
        // a guess here is what would put a permanently-broken button on the
        // login screen; nil is what keeps it off.
        XCTAssertNil(GoogleAuthConfig.callbackScheme(forClientId: "not-a-google-client-id"))
        XCTAssertNil(GoogleAuthConfig.callbackScheme(forClientId: "https://accounts.google.com"))
        XCTAssertNil(GoogleAuthConfig.redirectURI(forClientId: "not-a-google-client-id"))
    }

    func testRefusesAbsentOrEmptyClientId() {
        XCTAssertNil(GoogleAuthConfig.callbackScheme(forClientId: nil))
        XCTAssertNil(GoogleAuthConfig.callbackScheme(forClientId: ""))
        // The suffix alone, with nothing in front of it, is not a client id.
        XCTAssertNil(GoogleAuthConfig.callbackScheme(forClientId: ".apps.googleusercontent.com"))
    }

    /// This build ships no client id, so the button must not render. If this
    /// ever fails it means someone committed a real client id into
    /// `project.yml` — at which point the backend needs `GOOGLE_IOS_CLIENT_ID`
    /// set to the same value, and this expectation should be inverted rather
    /// than deleted.
    func testThisBuildIsDeliberatelyUnconfigured() {
        XCTAssertFalse(GoogleAuthConfig.isConfigured)
        XCTAssertNil(GoogleAuthConfig.clientId)
    }
}
