import XCTest
@testable import Empyralis

/// Markdown tables. Before this, a table's lines fell through to the
/// paragraph branch and were joined with spaces, so the document reader
/// showed a run-on line of raw pipes — leaked source. Three of the four
/// documents in a real seeded workspace carry a table, and one of them is a
/// release checklist whose own row reads "Document reader | Renders tables
/// and code blocks".
final class MarkdownTableTests: XCTestCase {

    /// Verbatim from the seeded "iOS Release Checklist" document.
    private let realDocument = """
    ## Screens that must be verified

    | Screen | What must be true | Owner |
    | --- | --- | --- |
    | Sign in | Keyboard survives rotation | Iris |
    | Task board | Opens at the newest item | Iris |
    | Document reader | Renders tables and code blocks | Iris |

    ## Build command
    """

    private func firstTable(_ markdown: String) -> (header: [String], rows: [[String]])? {
        for block in MarkdownBlock.parse(markdown) {
            if case let .table(header, rows) = block { return (header, rows) }
        }
        return nil
    }

    func testParsesTheRealDocumentsTable() throws {
        let table = try XCTUnwrap(firstTable(realDocument))
        XCTAssertEqual(table.header, ["Screen", "What must be true", "Owner"])
        XCTAssertEqual(table.rows.count, 3)
        XCTAssertEqual(table.rows[0], ["Sign in", "Keyboard survives rotation", "Iris"])
        XCTAssertEqual(table.rows[2], ["Document reader", "Renders tables and code blocks", "Iris"])
    }

    /// The blocks either side must survive — a table must not swallow the
    /// heading above it or the one below it.
    func testSurroundingBlocksSurvive() {
        let blocks = MarkdownBlock.parse(realDocument)
        guard case let .heading(level, text) = blocks.first else {
            return XCTFail("expected a leading heading, got \(String(describing: blocks.first))")
        }
        XCTAssertEqual(level, 2)
        XCTAssertEqual(text, "Screens that must be verified")

        guard case let .heading(_, last) = blocks.last else {
            return XCTFail("expected a trailing heading, got \(String(describing: blocks.last))")
        }
        XCTAssertEqual(last, "Build command")
    }

    /// THE NEGATIVE CASE, and it is the one that matters. A separator row is
    /// the only thing that promotes a pipe-bearing line to a table; ordinary
    /// prose containing a pipe must stay a paragraph.
    func testProseContainingAPipeIsNotATable() {
        let blocks = MarkdownBlock.parse("The cost is 5 | 10 depending on the plan.\nStill the same paragraph.")
        XCTAssertNil(firstTable("The cost is 5 | 10 depending on the plan.\nStill the same paragraph."))
        XCTAssertEqual(blocks.count, 1)
        guard case .paragraph = blocks[0] else {
            return XCTFail("expected a paragraph, got \(blocks[0])")
        }
    }

    func testHeaderWithNoBodyRowsIsStillATable() throws {
        let table = try XCTUnwrap(firstTable("| A | B |\n| --- | --- |"))
        XCTAssertEqual(table.header, ["A", "B"])
        XCTAssertTrue(table.rows.isEmpty)
    }

    func testAlignmentMarkersAreAcceptedAsASeparator() throws {
        let table = try XCTUnwrap(firstTable("| A | B | C |\n| :--- | :---: | ---: |\n| 1 | 2 | 3 |"))
        XCTAssertEqual(table.rows, [["1", "2", "3"]])
    }

    func testRowWrittenWithoutOuterPipes() throws {
        let table = try XCTUnwrap(firstTable("A | B\n--- | ---\n1 | 2"))
        XCTAssertEqual(table.header, ["A", "B"])
        XCTAssertEqual(table.rows, [["1", "2"]])
    }

    func testEscapedPipeStaysInsideItsCell() {
        XCTAssertEqual(markdownSplitRow("| a \\| b | c |"), ["a | b", "c"])
    }

    /// A short row must not crash or borrow the next column's header.
    func testRaggedRowIsKeptAsIs() throws {
        let table = try XCTUnwrap(firstTable("| A | B | C |\n|---|---|---|\n| 1 | 2 |"))
        XCTAssertEqual(table.rows, [["1", "2"]])
    }

    func testSeparatorAloneIsNotATable() {
        XCTAssertNil(firstTable("| --- | --- |"))
    }

    func testCodeFenceContainingPipesIsStillCode() {
        let blocks = MarkdownBlock.parse("```\n| a | b |\n| --- | --- |\n```")
        XCTAssertEqual(blocks.count, 1)
        guard case let .code(lines, _) = blocks[0] else {
            return XCTFail("a fenced block must win over table detection, got \(blocks[0])")
        }
        XCTAssertEqual(lines, ["| a | b |", "| --- | --- |"])
    }
}
