import Foundation

/// Single source of truth for which backend this build talks to.
/// Production is empyralis.ai; swap to a local disposable stack for dev
/// (never point this at anyone's real database from a device build).
enum APIConfig {
    /// Production. The only URL a shipped build may ever talk to.
    private static let productionURL = URL(string: "https://empyralis.ai/api")!

    /// A DEBUG build talks to a local disposable stack by default, because a
    /// developer build pointed at production is one careless tap away from
    /// writing into real customer data. Release is untouched.
    ///
    /// Override in either configuration with an `EMPYRALIS_API_BASE_URL`
    /// environment variable on the scheme (Xcode ▸ Edit Scheme ▸ Run ▸
    /// Arguments) — that is how you demo a DEBUG build against production
    /// without editing this file and forgetting to put it back.
    static let baseURL: URL = {
        if let raw = ProcessInfo.processInfo.environment["EMPYRALIS_API_BASE_URL"],
           let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
           url.scheme != nil {
            return url
        }
        #if DEBUG
        return URL(string: "http://127.0.0.1:8001/api")!
        #else
        return productionURL
        #endif
    }()

    /// The WEBSITE origin — where `/login` (the Safari-based sign-in page,
    /// see `NativeWebLogin`) is actually served. Deliberately NOT derived
    /// from `baseURL`: in production the two share a host but not a path
    /// (`empyralis.ai/api` vs `empyralis.ai`), while a local DEBUG stack
    /// runs them on entirely different ports — the frontend's own
    /// `npm run dev` is `next dev -H localhost -p 3000`, unrelated to
    /// `baseURL`'s 127.0.0.1:8001 backend default below.
    ///
    /// Same override pattern as `baseURL`: set `EMPYRALIS_WEB_ORIGIN` on the
    /// scheme to point a DEBUG build at a differently-ported local frontend
    /// without editing this file.
    static let webOrigin: URL = {
        if let raw = ProcessInfo.processInfo.environment["EMPYRALIS_WEB_ORIGIN"],
           let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
           url.scheme != nil {
            return url
        }
        #if DEBUG
        return URL(string: "http://127.0.0.1:3000")!
        #else
        return URL(string: "https://empyralis.ai")!
        #endif
    }()

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
