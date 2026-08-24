import UIKit
import UserNotifications

/// SwiftUI has no native hook for the APNs device-token callbacks, so a
/// UIApplicationDelegate is still required. It owns nothing — it forwards
/// to PushManager and DeepLinkRouter, which are injected once at launch.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {

    /// Set by EmpyralisApp at construction. Weak-ish by convention: these
    /// are @StateObjects owned by the App and outlive the delegate.
    static var pushManager: PushManager?
    static var deepLinkRouter: DeepLinkRouter?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        Task { @MainActor in
            Self.pushManager?.didRegister(deviceToken: deviceToken)
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        Task { @MainActor in
            Self.pushManager?.didFailToRegister(error: error)
        }
    }

    /// A notification arriving while the app is open. Shown as a banner
    /// deliberately: this app's notifications are about work that needs
    /// attention, and silently swallowing one because the app happens to
    /// be foregrounded means the person never learns it happened.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    /// The person tapped a notification. Route to whatever it points at.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let info = response.notification.request.content.userInfo
        await MainActor.run {
            Self.deepLinkRouter?.handle(notificationUserInfo: info)
        }
    }
}
