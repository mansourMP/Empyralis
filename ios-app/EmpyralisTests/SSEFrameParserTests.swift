import XCTest
@testable import Empyralis

/// SSE framing. This guards a bug that shipped and was invisible from the
/// code: the reader used `URLSession.AsyncBytes.lines`, which does not yield
/// the BLANK lines that terminate SSE records. Measured against the real
/// backend, a three-event trace arrived as six lines with no blank among
/// them, so ZERO records were emitted, the terminal `trace.failed` was never
/// seen, and every agent's Work view said "the connection closed before the
/// run reported an outcome" no matter what the run had done.
final class SSEFrameParserTests: XCTestCase {

    private func records(_ wire: String) -> [SSEClient.Record] {
        var parser = SSEFrameParser()
        var out: [SSEClient.Record] = []
        for byte in Array(wire.utf8) { out.append(contentsOf: parser.consume(byte)) }
        out.append(contentsOf: parser.finish())
        return out
    }

    /// Verbatim framing from `GET /api/agent-traces/{id}/stream`, trimmed to
    /// the fields that matter. THE BLANK LINES ARE THE TEST.
    ///
    /// The explicit trailing "\n" is load-bearing and is not padding: a Swift
    /// multi-line literal does NOT end with a newline, so without it the last
    /// record's terminating blank line is never delivered and the fixture
    /// under-reports by one record. The real server does terminate it —
    /// measured on the live endpoint, 3 records and 3 blank lines.
    private let realWire = """
    event: trace
    data: {"seq": 1, "event_type": "trace.started"}

    event: trace
    data: {"seq": 2, "event_type": "trace.routed"}

    event: trace
    data: {"seq": 3, "event_type": "trace.failed"}

    """ + "\n"

    func testEachEventBecomesItsOwnRecord() {
        let out = records(realWire)
        XCTAssertEqual(out.count, 3, "blank lines must terminate records")
        XCTAssertEqual(out.map(\.event), ["trace", "trace", "trace"])
    }

    /// Each record's data must be ONE valid JSON object. Under the old
    /// reader all three arrived concatenated, which JSONDecoder rejects —
    /// and that rejection was swallowed by a `try?`, which is why nothing
    /// anywhere reported the problem.
    func testEachRecordIsOneParseableJSONObject() throws {
        for record in records(realWire) {
            let object = try JSONSerialization.jsonObject(with: Data(record.data.utf8)) as? [String: Any]
            XCTAssertNotNil(object, "record data was not a single JSON object: \(record.data)")
        }
    }

    func testTerminalEventSurvivesFraming() {
        let types = records(realWire).compactMap { record -> String? in
            let object = try? JSONSerialization.jsonObject(with: Data(record.data.utf8)) as? [String: Any]
            return (object as? [String: Any])?["event_type"] as? String
        }
        XCTAssertEqual(types, ["trace.started", "trace.routed", "trace.failed"])
    }

    /// The backend sends a `:` comment every 15s as a keepalive. It must not
    /// become data, and it must not terminate a record on its own.
    func testKeepaliveCommentIsIgnored() {
        let out = records(": ping\n\nevent: trace\ndata: {\"a\":1}\n\n")
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].data, "{\"a\":1}")
    }

    func testMultipleDataLinesJoinWithNewline() {
        let out = records("data: line one\ndata: line two\n\n")
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].data, "line one\nline two")
        XCTAssertNil(out[0].event)
    }

    func testCRLFFramingIsAccepted() {
        let out = records("event: trace\r\ndata: {\"a\":1}\r\n\r\n")
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].data, "{\"a\":1}")
        XCTAssertEqual(out[0].event, "trace")
    }

    /// A record split across byte deliveries must still come out whole —
    /// this is a byte-at-a-time parser precisely so a chunk boundary in the
    /// middle of a line cannot lose data.
    func testRecordSplitAcrossArbitraryByteBoundariesIsWhole() {
        var parser = SSEFrameParser()
        var out: [SSEClient.Record] = []
        for byte in Array("event: trace\ndata: {\"seq\":7}\n\n".utf8) {
            out.append(contentsOf: parser.consume(byte))
        }
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].data, "{\"seq\":7}")
    }

    /// A stream that ends mid-record must not emit a truncated event —
    /// half a JSON object is worse than no event.
    func testTruncatedTrailingRecordIsNotEmitted() {
        let out = records("event: trace\ndata: {\"seq\":9}")
        XCTAssertTrue(out.isEmpty, "an unterminated record must not be handed to the caller")
    }

    func testBlankLineWithNoDataEmitsNothing() {
        XCTAssertTrue(records("\n\n\n").isEmpty)
        XCTAssertTrue(records("event: trace\n\n").isEmpty, "an event: with no data is not a record")
    }

    func testLeadingSpaceAfterColonIsStrippedOnlyOnce() {
        let out = records("data:  two spaces\n\n")
        XCTAssertEqual(out[0].data, " two spaces")
    }
}
