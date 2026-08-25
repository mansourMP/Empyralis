import Foundation

enum APIError: Error {
    case unauthorized
    case server(String)
    case decoding
}

/// Thin REST client. One responsibility: attach the bearer token, decode
/// snake_case JSON, and surface a typed unauthorized case so SessionStore
/// can decide whether to refresh or sign out — this file never decides that
/// itself.
final class APIClient {
    static let shared = APIClient()
    private let session = URLSession(configuration: .default)
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        return d
    }()

    var bearerToken: String?

    private init() {}

    func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        try await request(path: path, method: "GET", query: query, body: nil)
    }

    func post<T: Decodable>(_ path: String, body: [String: Any]) async throws -> T {
        try await request(path: path, method: "POST", query: [:], body: body)
    }

    func patch<T: Decodable>(_ path: String, body: [String: Any]) async throws -> T {
        try await request(path: path, method: "PATCH", query: [:], body: body)
    }

    private func request<T: Decodable>(
        path: String,
        method: String,
        query: [String: String],
        body: [String: Any]?
    ) async throws -> T {
        var components = URLComponents(url: APIConfig.baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        var req = URLRequest(url: components.url!)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = bearerToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }

        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.server("No response")
        }
        if http.statusCode == 401 {
            throw APIError.unauthorized
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = (try? JSONDecoder().decode(ServerErrorBody.self, from: data))?.detail
            throw APIError.server(message ?? "Request failed (\(http.statusCode))")
        }
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            // A decode failure on a 2xx is a CONTRACT mismatch between this
            // app and the route — and callers wrap these fetches in `try?`,
            // so the throw becomes an empty list and the screen tells a
            // plausible lie instead of reporting anything. That is exactly
            // how `Agent` decoded its id from a key the route never sends,
            // and the Agents tab read "No agents yet" for months. Name it in
            // DEBUG so the next one is one console line rather than an
            // investigation.
            #if DEBUG
            print("[APIClient] DECODE FAILED \(method) \(path) as \(T.self): \(error)")
            #endif
            throw APIError.decoding
        }
    }
}

private struct ServerErrorBody: Decodable {
    let detail: String?
}
