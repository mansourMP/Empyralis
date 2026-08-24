import Foundation

/// Disk persistence for the local-first store. JSON in Application Support,
/// excluded from iCloud backup (it's a rebuildable cache of server state,
/// not user data — backing it up would restore a stale workspace onto a new
/// phone and make "why is this wrong" unanswerable).
///
/// Deliberately NOT SQLite/SwiftData: the working set here is a few hundred
/// tasks and a few dozen agents, which fits in memory trivially. A database
/// buys query power this app has no use for and costs a migration story on
/// every model change. Revisit if a workspace ever holds tens of thousands
/// of rows — not before.
enum DiskCache {
    private static var directory: URL = {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let dir = base.appendingPathComponent("EmpyralisCache", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var excluded = URLResourceValues()
        excluded.isExcludedFromBackup = true
        var mutableDir = dir
        try? mutableDir.setResourceValues(excluded)
        return dir
    }()

    static func save<T: Encodable>(_ value: T, as key: String) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        try? data.write(to: directory.appendingPathComponent("\(key).json"), options: .atomic)
    }

    static func load<T: Decodable>(_ type: T.Type, from key: String) -> T? {
        let url = directory.appendingPathComponent("\(key).json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    /// Called on sign-out. A cache that outlives its session is a data leak
    /// on a shared or resold device — the tokens are already gone from the
    /// Keychain by then, but the content would still be sitting here.
    static func clearAll() {
        guard let files = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) else { return }
        for file in files {
            try? FileManager.default.removeItem(at: file)
        }
    }
}
