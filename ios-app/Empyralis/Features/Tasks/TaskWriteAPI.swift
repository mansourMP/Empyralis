import Foundation

/// ONE method, and it exists for a boundary reason rather than a technical
/// one: detaching a label is `DELETE /w/{ws}/fleet/tasks/{id}/labels/{label}`,
/// and the shared `APIClient` carries `get`/`post`/`patch` but no `delete`.
/// APIClient.swift is owned by another workstream in flight, so adding a
/// method there would be an edit to a file this change may not touch — and
/// the alternative, shipping labels you can attach but never remove, is a
/// half-control, which is worse than a small duplicate.
///
/// FOLD THIS INTO `APIClient` when both workstreams have landed. It is a
/// deliberate, documented duplicate of that type's request pipeline —
/// same bearer header, same typed `unauthorized` case so SessionStore keeps
/// making the refresh-or-sign-out decision, same non-2xx-before-decode
/// ordering (which is what lets the store treat a `.decoding` throw as
/// "the write landed, the reply was unreadable" rather than as a failure).
/// If that ordering changes here and not there, the store's honesty
/// branches quietly stop meaning what they say.
enum TaskWriteAPI {

    static func delete<T: Decodable>(_ path: String) async throws -> T {
        let url = APIConfig.baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = APIClient.shared.bearerToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.server("No response")
        }
        if http.statusCode == 401 {
            throw APIError.unauthorized
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = (try? JSONDecoder().decode(DeleteErrorBody.self, from: data))?.detail
            throw APIError.server(message ?? "Request failed (\(http.statusCode))")
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding
        }
    }
}

private struct DeleteErrorBody: Decodable {
    let detail: String?
}
