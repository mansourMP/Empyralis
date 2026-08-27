import XCTest
@testable import Empyralis

/// Document images. Before this, `![alt](url)` had no block case at all —
/// `MarkdownBlock.parse` fell through to `.paragraph`, and `AttributedString
/// (markdown:)` cannot render an inline image inside a SwiftUI `Text`, so
/// the picture silently vanished with nothing telling the reader one was
/// there. This file covers the parser (standalone-line detection) and the
/// URL policy (`DocumentImageURLPolicy`), which must match the web app's
/// `safeMarkdownLiteImageSrc` (frontend/lib/workspace/markdown-lite.tsx)
/// exactly on what it allows, and may only ever be narrower on what it
/// refuses — never wider.
final class MarkdownImageTests: XCTestCase {

    // MARK: - Parser: standalone-line detection

    private func firstImage(_ markdown: String) -> (alt: String, url: String)? {
        for block in MarkdownBlock.parse(markdown) {
            if case let .image(alt, url) = block { return (alt, url) }
        }
        return nil
    }

    func testParsesAStandaloneImageLine() throws {
        let image = try XCTUnwrap(firstImage("![A screenshot](https://example.com/pic.png)"))
        XCTAssertEqual(image.alt, "A screenshot")
        XCTAssertEqual(image.url, "https://example.com/pic.png")
    }

    func testEmptyAltIsAllowed() throws {
        let image = try XCTUnwrap(firstImage("![](https://example.com/pic.png)"))
        XCTAssertEqual(image.alt, "")
        XCTAssertEqual(image.url, "https://example.com/pic.png")
    }

    /// The real seeded verification document: a heading, prose, an image,
    /// another heading, prose, a second image, a third, then a closing
    /// paragraph. Every image must be found and the headings on either side
    /// must survive — an image block must not swallow a neighbour, the same
    /// bar MarkdownTableTests holds a table block to.
    func testRealDocumentAllThreeImagesAndSurroundingHeadingsSurvive() {
        let markdown = """
        # Image Rendering Verification

        This document exercises the three honest states.

        ## A working image

        A small placeholder icon.

        ![A small placeholder icon](https://placehold.co/64x64.png)

        ## A broken link

        This URL resolves but returns 404.

        ![This image link is broken](https://httpstat.us/404)

        ## A refused SVG

        SVGs are refused by policy.

        ![An SVG logo, blocked because SVGs are refused by policy](https://upload.wikimedia.org/wikipedia/commons/4/4a/Commons-logo.svg)

        ## After the images

        Ordinary paragraph text confirms the document keeps rendering normally.
        """

        let blocks = MarkdownBlock.parse(markdown)
        let images: [(alt: String, url: String)] = blocks.compactMap {
            if case let .image(alt, url) = $0 { return (alt, url) }
            return nil
        }
        XCTAssertEqual(images.count, 3)
        XCTAssertEqual(images[0].url, "https://placehold.co/64x64.png")
        XCTAssertEqual(images[1].url, "https://httpstat.us/404")
        XCTAssertEqual(images[2].url, "https://upload.wikimedia.org/wikipedia/commons/4/4a/Commons-logo.svg")

        let headingTexts = blocks.compactMap { block -> String? in
            if case let .heading(_, text) = block { return text }
            return nil
        }
        XCTAssertEqual(headingTexts, [
            "Image Rendering Verification",
            "A working image",
            "A broken link",
            "A refused SVG",
            "After the images",
        ])

        // The closing paragraph must still be there, and still a paragraph.
        guard case .paragraph = blocks.last else {
            return XCTFail("expected the document to end in a paragraph, got \(String(describing: blocks.last))")
        }
    }

    /// THE NEGATIVE CASE. An image mixed into a line with other text is not
    /// a standalone image line, so it must fall through to a paragraph —
    /// never crash, never silently vanish the surrounding text.
    func testImageMixedWithOtherTextOnTheSameLineIsNotAStandaloneImage() {
        XCTAssertNil(firstImage("See this: ![alt](https://example.com/a.png) done."))
        let blocks = MarkdownBlock.parse("See this: ![alt](https://example.com/a.png) done.")
        guard case .paragraph = blocks.first else {
            return XCTFail("expected a paragraph, got \(String(describing: blocks.first))")
        }
    }

    /// Trailing characters after the closing paren disqualify the line —
    /// this is not "the entire line is the image."
    func testTrailingTextAfterTheImageIsNotAStandaloneImage() {
        XCTAssertNil(firstImage("![alt](https://example.com/a.png) extra"))
    }

    /// A bare pipe-free, hash-free ordinary paragraph must never be
    /// misdetected as an image just because it starts with "!".
    func testAnOrdinaryExclamationIsNotAnImage() {
        XCTAssertNil(firstImage("!Important: read this carefully."))
    }

    func testUnterminatedImageSyntaxFallsThroughToParagraph() {
        XCTAssertNil(firstImage("![alt](https://example.com/a.png"))
        let blocks = MarkdownBlock.parse("![alt](https://example.com/a.png")
        guard case .paragraph = blocks.first else {
            return XCTFail("expected a paragraph fallback, got \(String(describing: blocks.first))")
        }
    }

    // MARK: - URL policy: allowed

    func testHttpsIsAllowed() {
        XCTAssertNotNil(DocumentImageURLPolicy.resolvedImageURL("https://example.com/pic.png"))
    }

    func testHttpIsAllowed() {
        XCTAssertNotNil(DocumentImageURLPolicy.resolvedImageURL("http://example.com/pic.png"))
    }

    func testSchemeMatchingIsCaseInsensitive() {
        XCTAssertNotNil(DocumentImageURLPolicy.resolvedImageURL("HTTPS://example.com/pic.png"))
    }

    func testAQueryStringEndingInSvgTextIsNotTreatedAsAnSvgPath() {
        // The PATH is .png; the query merely mentions svg. Same distinction
        // the web's own hasSvgExtension makes.
        XCTAssertNotNil(DocumentImageURLPolicy.resolvedImageURL("https://example.com/pic.png?x=bar.svg"))
    }

    // MARK: - URL policy: the .svg refusal — the sharpest security case

    func testSvgIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("https://example.com/logo.svg"))
    }

    func testSvgIsRefusedCaseInsensitively() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("https://example.com/logo.SVG"))
    }

    func testSvgIsRefusedWithAQueryString() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("https://example.com/logo.svg?v=2"))
    }

    func testSvgIsRefusedWithAFragment() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("https://example.com/logo.svg#frag"))
    }

    func testSvgIsRefusedWithBothQueryAndFragment() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("https://example.com/logo.svg?v=2#frag"))
    }

    func testTheRealSeededSvgUrlIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL(
            "https://upload.wikimedia.org/wikipedia/commons/4/4a/Commons-logo.svg"
        ))
    }

    func testHttpSvgIsAlsoRefused() {
        // The .svg refusal applies regardless of an otherwise-allowed scheme.
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("http://example.com/logo.svg"))
    }

    // MARK: - URL policy: disallowed schemes

    func testJavascriptSchemeIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("javascript:alert(1)"))
    }

    func testDataSchemeIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("data:image/png;base64,AAAA"))
    }

    func testFileSchemeIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("file:///etc/passwd"))
    }

    func testFtpSchemeIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("ftp://example.com/pic.png"))
    }

    /// A tab hidden inside the scheme is a classic filter-bypass trick
    /// ("java\tscript:") — cleaned first, and then still refused since the
    /// cleaned scheme is "javascript", not an allowed one.
    func testATabHiddenInTheSchemeDoesNotBypassTheRefusal() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("java\tscript:alert(1)"))
    }

    // MARK: - URL policy: malformed / absent

    func testEmptyStringIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL(""))
    }

    func testWhitespaceOnlyIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("   "))
    }

    func testASchemeLessStringIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("example.com/pic.png"))
    }

    /// Narrower than the web on purpose: this app has no document base URL
    /// to resolve a relative path against, so one is refused rather than
    /// guessed at — see DocumentImageURLPolicy's own header comment.
    func testARootRelativePathIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("/uploads/pic.png"))
    }

    /// Protocol-relative (`//host/...`) silently changes host in a browser
    /// context; refused here too since it carries no explicit scheme.
    func testProtocolRelativeIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("//example.com/pic.png"))
    }

    func testAnHttpsSchemeWithNoHostIsRefused() {
        XCTAssertNil(DocumentImageURLPolicy.resolvedImageURL("https:///pic.png"))
    }

    // MARK: - URL policy: cleanup

    func testSurroundingWhitespaceIsTrimmed() {
        XCTAssertNotNil(DocumentImageURLPolicy.resolvedImageURL("  https://example.com/pic.png  "))
    }

    func testEmbeddedNewlinesAreStripped() {
        // "https://exa\nmple.com/pic.png" -> "https://example.com/pic.png"
        XCTAssertNotNil(DocumentImageURLPolicy.resolvedImageURL("https://exa\nmple.com/pic.png"))
    }
}
