import Foundation
import UIKit
import UserNotifications

/// Push registration, with the authorization state modelled HONESTLY —
/// which is the whole difficulty of this feature on iOS.
///
/// There are five real states and collapsing any two of them produces a
/// control that lies:
///
///   notDetermined  never asked. We may ask, and the OS will show a prompt.
///   authorized     on, and tokens are flowing.
///   denied         the person said no. THE OS WILL NEVER PROMPT AGAIN —
///                  asking again is a no-op that silently does nothing, so
///                  the only honest action left is a link to iOS Settings.
///   provisional    quiet delivery, granted without a prompt.
///   registering    permission granted, no APNs token back yet. NOT the
///                  same as authorized: there is nothing to notify yet.
///
/// A single "notifications on/off" toggle cannot express `denied`, and that
/// is exactly the case where a toggle would flip, appear on, and deliver
/// nothing forever.
@MainActor
final class PushManager: NSObject, ObservableObject {

    enum State: Equatable {
        case notDetermined
        case registering
        case authorized
        case provisional
        case denied
        /// We asked the OS and it refused for a reason that isn't "the
        /// person said no" — distinct, because retrying may actually work.
        case failed(String)
    }

    @Published private(set) var state: State = .notDetermined

    private var deviceToken: String?
    private unowned let session: SessionStore

    init(session: SessionStore) {
        self.session = session
        super.init()
    }

    /// Reads the OS's current setting without prompting. Safe to call on
    /// every appearance — this is what keeps the Settings row true after
    /// someone changes the permission in iOS Settings and comes back.
    func refreshAuthorizationState() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        switch settings.authorizationStatus {
        case .notDetermined:
            state = .notDetermined
        case .denied:
            state = .denied
        case .authorized:
            state = deviceToken == nil ? .registering : .authorized
            registerWithAPNs()
        case .provisional:
            state = .provisional
            registerWithAPNs()
        case .ephemeral:
            state = .authorized
        @unknown default:
            state = .notDetermined
        }
    }

    /// Only ever called from an explicit action — never on launch. A cold
    /// permission prompt before anyone has seen why they'd want it is how
    /// an app gets permanently denied on first run.
    func requestAuthorization() async {
        do {
            let granted = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
            if granted {
                state = .registering
                registerWithAPNs()
            } else {
                state = .denied
            }
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    private func registerWithAPNs() {
        UIApplication.shared.registerForRemoteNotifications()
    }

    // MARK: - Token plumbing (called from the app delegate)

    func didRegister(deviceToken raw: Data) {
        let token = raw.map { String(format: "%02x", $0) }.joined()
        deviceToken = token
        state = .authorized
        Task { await sendTokenToServer(token) }
    }

    func didFailToRegister(error: Error) {
        // Registration failing is NOT "denied" — the person said yes and
        // something else broke. Saying "denied" would send them to iOS
        // Settings to fix a permission that is already correct.
        state = .failed(error.localizedDescription)
    }

    private func sendTokenToServer(_ token: String) async {
        guard let workspaceId = session.currentWorkspaceId else { return }

        struct Ack: Decodable { let ok: Bool }
        do {
            let _: Ack = try await APIClient.shared.post("/push/devices", body: [
                "device_token": token,
                "platform": "ios",
                "workspace_id": workspaceId,
                "bundle_id": Bundle.main.bundleIdentifier ?? "ai.empyralis.app",
                "environment": Self.apnsEnvironment,
            ])
        } catch {
            // Deliberately silent to the person: the OS permission is
            // genuinely granted, and a failed handshake with our own server
            // is ours to retry, not a thing to interrupt them about. It
            // retries on next foreground via refreshAuthorizationState().
        }
    }

    /// A DEBUG build's token is minted by APNs' sandbox and is rejected
    /// outright by the production gateway, so the server has to be told
    /// which one it came from. Getting this wrong looks exactly like a
    /// dead token, with nothing saying why.
    private static var apnsEnvironment: String {
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
    }
}
