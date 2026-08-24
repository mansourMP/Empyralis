import Foundation

/// Single source of truth for which backend this build talks to.
/// Production is empyralis.ai; swap to a local disposable stack for dev
/// (never point this at anyone's real database from a device build).
enum APIConfig {
    static let baseURL = URL(string: "https://empyralis.ai/api")!

    static let devicePlatform = "ios"
    static var deviceName: String {
        #if canImport(UIKit)
        return UIDevice.current.name
        #else
        return "iPhone"
        #endif
    }
}

#if canImport(UIKit)
import UIKit
#endif
