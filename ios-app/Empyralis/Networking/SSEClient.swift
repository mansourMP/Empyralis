import Foundation

/// A minimal Server-Sent-Events reader built on URLSession's `bytes(for:)`
/// async sequence. No third-party dependencies — this project has zero SPM
/// packages and stays that way.
///
/// Framing follows the SSE spec as `sse_starlette` emits it on the backend:
/// a record is a run of `field: value` lines terminated by a BLANK line.
/// `data:` lines accumulate (joined by "\n"); `event:` names the record;
/// a line starting with `:` is a comment — the backend sends one every 15s
/// as a keepalive ping and it must never be parsed as data.
///
/// CANCELLATION IS THE POINT: the whole read loop lives inside the caller's
/// Task. When that Task is cancelled (a view disappearing), the `for try
/// await` on the byte stream throws CancellationError, the loop unwinds, and
/// URLSession tears the connection down. Nothing is leaked and no `finally`
/// bookkeeping is required — which is exactly why this is written as a
/// throwing AsyncStream over `bytes(for:)` rather than a delegate class that
/// would have to remember to cancel its own task.
enum SSEClient {

    /// One decoded record off the wire. `event` is nil for an unnamed record.
    struct Record {
        let event: String?
        let data: String
    }

    enum SSEError: Error, LocalizedError {
        /// The server answered, but not with a stream we can read.
        case http(status: Int, message: String?)
        case unauthorized
        /// The connection never came up at all (no response).
        case transport(String)

        var errorDescription: String? {
            switch self {
            case .unauthorized:
                return "Your session expired."
            case .http(let status, let message):
                return message ?? "The server refused the stream (\(status))."
            case .transport(let detail):
                return detail
            }
        }
    }

    /// Opens `path` (relative to `APIConfig.baseURL`) and yields each SSE
    /// record as it arrives. Throws before yielding anything if the stream
    /// could not be established — so a caller can tell "couldn't connect"
    /// from "connected, nothing has happened yet", which are different facts
    /// and must never share one message.
    static func records(
        path: String,
        query: [String: String] = [:],
        session: URLSession = .shared
    ) -> AsyncThrowingStream<Record, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var components = URLComponents(
                        url: APIConfig.baseURL.appendingPathComponent(path),
                        resolvingAgainstBaseURL: false
                    )!
                    if !query.isEmpty {
                        components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
                    }
                    var request = URLRequest(url: components.url!)
                    request.httpMethod = "GET"
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
                    if let token = APIClient.shared.bearerToken {
                        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                    }
                    // An SSE connection is open for as long as the work lasts;
                    // the default 60s resource timeout would sever a healthy
                    // stream mid-run. The per-read timeout still applies.
                    request.timeoutInterval = 3600

                    let (bytes, response) = try await session.bytes(for: request)

                    guard let http = response as? HTTPURLResponse else {
                        throw SSEError.transport("No response from the server.")
                    }
                    if http.statusCode == 401 {
                        throw SSEError.unauthorized
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        throw SSEError.http(status: http.statusCode, message: nil)
                    }

                    // SPLIT THE BYTES OURSELVES — see SSEFrameParser.
                    var parser = SSEFrameParser()
                    for try await byte in bytes {
                        try Task.checkCancellation()
                        for record in parser.consume(byte) { continuation.yield(record) }
                    }
                    // The server closed the stream. `finish()` flushes a final
                    // line that carried no trailing newline.
                    for record in parser.finish() { continuation.yield(record) }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch let error as SSEError {
                    continuation.finish(throwing: error)
                } catch {
                    if (error as? URLError)?.code == .cancelled {
                        continuation.finish()
                    } else {
                        continuation.finish(throwing: SSEError.transport(error.localizedDescription))
                    }
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

/// SSE framing, as a pure state machine over bytes so it can be tested
/// without a network.
///
/// IT EXISTS BECAUSE `URLSession.AsyncBytes.lines` DESTROYS SSE FRAMING.
/// `bytes.lines` looks like exactly the right tool and is not: it does not
/// yield the BLANK lines, and in SSE the blank line is the only thing that
/// terminates a record.
///
/// Measured against this backend, on a real trace carrying three events:
/// `bytes.lines` produced six lines — three `event:` and three `data:` — and
/// not one blank among them, so no record was ever emitted. The trailing
/// flush then handed the caller all three JSON objects concatenated, which
/// `JSONDecoder` rejects, so the terminal `trace.failed` was never seen and
/// every agent's Work view reported "the connection closed before the run
/// reported an outcome" regardless of what the run had actually done. The
/// same bytes through this parser yield 3 records and 3 blank lines.
struct SSEFrameParser {
    private var buffer: [UInt8] = []
    private var dataLines: [String] = []
    private var eventName: String?

    /// Feed one byte. Returns any records completed by it (0 or 1).
    mutating func consume(_ byte: UInt8) -> [SSEClient.Record] {
        guard byte == 0x0A else {              // not "\n"
            buffer.append(byte)
            return []
        }
        if buffer.last == 0x0D { buffer.removeLast() }   // CRLF
        let line = String(decoding: buffer, as: UTF8.self)
        buffer.removeAll(keepingCapacity: true)
        return consume(line: line)
    }

    /// End of stream: flush a final line that had no trailing newline.
    /// A half-finished record is NOT emitted — a truncated event is not an
    /// event, and emitting it would hand the caller unparseable JSON.
    mutating func finish() -> [SSEClient.Record] {
        guard !buffer.isEmpty else { return [] }
        let line = String(decoding: buffer, as: UTF8.self)
        buffer.removeAll(keepingCapacity: true)
        return consume(line: line)
    }

    private mutating func consume(line: String) -> [SSEClient.Record] {
        if line.isEmpty {
            // Blank line ends the record. A record carrying no data lines
            // (a bare comment run) is not emitted.
            defer { dataLines.removeAll(); eventName = nil }
            guard !dataLines.isEmpty else { return [] }
            return [SSEClient.Record(event: eventName, data: dataLines.joined(separator: "\n"))]
        }
        if line.hasPrefix(":") { return [] }   // keepalive comment
        guard let colon = line.firstIndex(of: ":") else {
            return []                           // a bare field name carries no value we use
        }
        let field = String(line[line.startIndex..<colon])
        var value = String(line[line.index(after: colon)...])
        if value.hasPrefix(" ") { value.removeFirst() }

        switch field {
        case "data": dataLines.append(value)
        case "event": eventName = value
        default: break                          // id/retry — not used here
        }
        return []
    }
}
