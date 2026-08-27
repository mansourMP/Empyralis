import SwiftUI
import UIKit

/// Renders the same markdown subset the web app's `markdown-lite.tsx`
/// supports, INCLUDING images — see `.image` below and `DocumentImageView`.
///
/// IMAGES ARE A STANDALONE-LINE BLOCK, NOT AN INLINE RUN. The web renders
/// `![alt](url)` inline (`markdown-lite.tsx`'s `renderInline`) because a
/// browser's `<img>` can sit inside a `<p>`. SwiftUI's `Text` has no
/// equivalent for an async-loaded image mixed into styled text, so an image
/// that stands alone on its own line (the overwhelming real-world shape —
/// this is how every markdown author/agent actually embeds a picture) is
/// promoted to its own block, exactly like a heading or a rule. An image
/// written mid-paragraph, mixed with other prose on the same line, is not
/// specially handled and falls through to plain inline text — a narrower
/// scope than the web, never a wider one.
///
/// TABLES ARE STACKED, NOT GRIDDED. A markdown table has no width budget on
/// a phone: three columns of prose either truncate to uselessness or force
/// the page to scroll sideways. So each ROW is rendered as a small record —
/// its first cell as the row's heading, every other cell as a
/// `column name / value` pair underneath. Nothing truncates, nothing
/// scrolls sideways, and the column meanings survive, which a squeezed grid
/// loses. Rendering them as paragraphs (what happened before) put raw
/// pipes on screen: three of the four seeded documents carry a table, and
/// one of them is the release checklist.
///
/// BLOCK STRUCTURE IS HAND-PARSED, INLINE FORMATTING IS NOT. SwiftUI's
/// `AttributedString(markdown:)` handles bold/italic/inline-code/links
/// correctly and is already on the platform — but it flattens block
/// structure (a heading loses its size, a list loses its bullet), so it is
/// used ONLY per-line, after this file has decided what kind of line it is.
/// Zero third-party dependencies, and the project stays that way.
///
/// LINKS ARE NEVER THE ACCENT. `Theme.accent` is reserved for the single
/// primary-action button in a view; a link is `textPrimary` plus an
/// underline, which is weight and shape rather than hue — the same rule the
/// web app enforces mechanically.
struct MarkdownRenderer: View {
    let markdown: String
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(alignment: .leading, spacing: Space.x3) {
            ForEach(Array(MarkdownBlock.parse(markdown).enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func blockView(_ block: MarkdownBlock) -> some View {
        switch block {
        case let .heading(level, text):
            inline(text)
                .font(headingFont(level))
                .foregroundStyle(Theme.textPrimary(scheme))
                .padding(.top, level <= 2 ? Space.x2 : 0)

        case let .paragraph(text):
            inline(text)
                .font(.empBody)
                .foregroundStyle(Theme.textPrimary(scheme))

        case let .quote(lines):
            HStack(alignment: .top, spacing: Space.x3) {
                Rectangle()
                    .fill(Theme.borderStrong(scheme))
                    .frame(width: 2)
                VStack(alignment: .leading, spacing: Space.x2) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        inline(line)
                            .font(.empBody)
                            .foregroundStyle(Theme.textSecondary(scheme))
                    }
                }
            }
            .fixedSize(horizontal: false, vertical: true)

        case let .code(lines, _):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(lines.joined(separator: "\n"))
                    .font(.empMono)
                    .foregroundStyle(Theme.textPrimary(scheme))
                    .textSelection(.enabled)
                    .padding(Space.x3)
            }
            .background(Theme.bgInset(scheme))
            .clipShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                    .stroke(Theme.border(scheme), lineWidth: 1)
            )

        case let .list(items, ordered):
            VStack(alignment: .leading, spacing: Space.x2) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: Space.x2) {
                        Text(ordered ? "\(index + 1)." : "•")
                            .font(.empBody)
                            .foregroundStyle(Theme.textMuted(scheme))
                            .frame(minWidth: ordered ? 20 : 12, alignment: .trailing)
                        inline(item.text)
                            .font(.empBody)
                            .foregroundStyle(Theme.textPrimary(scheme))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.leading, CGFloat(item.depth) * Space.x4)
                }
            }

        case let .table(header, rows):
            VStack(alignment: .leading, spacing: Space.x2) {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    tableRecord(header: header, row: row)
                }
            }

        case .rule:
            Rectangle()
                .fill(Theme.border(scheme))
                .frame(height: 1)
                .padding(.vertical, Space.x1)

        case let .image(alt, url):
            DocumentImageView(alt: alt, url: url)
        }
    }

    /// One table row as a record. The first cell is the heading because in
    /// every real table the first column is what the row IS; the rest are
    /// labelled so a reader still knows which column a value came from.
    private func tableRecord(header: [String], row: [String]) -> some View {
        let title = row.first ?? ""
        let rest = Array(row.dropFirst())

        return VStack(alignment: .leading, spacing: Space.x2) {
            if !title.isEmpty {
                inline(title)
                    .font(.empBodyMedium)
                    .foregroundStyle(Theme.textPrimary(scheme))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            ForEach(Array(rest.enumerated()), id: \.offset) { index, cell in
                if !cell.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        // A header cell can legitimately be blank; then the
                        // value stands alone rather than under an empty label.
                        if index + 1 < header.count, !header[index + 1].isEmpty {
                            Text(header[index + 1].uppercased())
                                .font(.empSectionHeader)
                                .foregroundStyle(Theme.textMuted(scheme))
                        }
                        inline(cell)
                            .font(.empSecondary)
                            .foregroundStyle(Theme.textSecondary(scheme))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
        .padding(Space.x3)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.card))
    }

    // Scales via Theme's empHeading1-4 (UIFontMetrics, same mechanism as
    // .empBody below) rather than the old raw .system(size:weight:), which
    // Apple documents as NOT participating in Dynamic Type at all — that
    // let a real H1 render smaller than its own paragraph text at large
    // accessibility sizes. See empHeading1-4's own doc comment in Theme.swift
    // for why H3/H4 deliberately share a curve rather than each level
    // getting its "obviously matching" title-class style.
    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .empHeading1
        case 2: return .empHeading2
        case 3: return .empHeading3
        default: return .empHeading4
        }
    }

    /// Inline formatting via the platform parser. `.inlineOnlyPreservingWhitespace`
    /// is what keeps it from swallowing block syntax we have already decided
    /// about; a parse failure falls back to the raw text rather than dropping
    /// the line, because showing the source beats showing nothing.
    private func inline(_ text: String) -> Text {
        if let attributed = try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return Text(styleLinks(attributed))
        }
        return Text(text)
    }

    /// Repaints link runs to `textPrimary` + underline. Left alone, SwiftUI
    /// paints them the system tint, which on this surface reads as an accent
    /// the accent law does not permit.
    private func styleLinks(_ input: AttributedString) -> AttributedString {
        var out = input
        for run in out.runs where run.link != nil {
            out[run.range].foregroundColor = Theme.textPrimary(scheme)
            out[run.range].underlineStyle = .single
        }
        return out
    }
}

// MARK: - Block parsing

/// The block vocabulary, deliberately the same set `markdown-lite.tsx`
/// recognises so the two products render the same document the same way.
enum MarkdownBlock: Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    case quote([String])
    case code(lines: [String], language: String)
    case list(items: [MarkdownListItem], ordered: Bool)
    case table(header: [String], rows: [[String]])
    case rule
    case image(alt: String, url: String)

    static func parse(_ markdown: String) -> [MarkdownBlock] {
        let lines = markdown
            .replacingOccurrences(of: "\r\n", with: "\n")
            .components(separatedBy: "\n")

        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var index = 0

        func flushParagraph() {
            guard !paragraph.isEmpty else { return }
            blocks.append(.paragraph(paragraph.joined(separator: " ")))
            paragraph.removeAll()
        }

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.isEmpty {
                flushParagraph()
                index += 1
                continue
            }

            // Fenced code. An UNTERMINATED fence consumes to end of input
            // rather than being dropped — the same call markdown-lite makes.
            if trimmed.hasPrefix("```") {
                flushParagraph()
                let language = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                index += 1
                var code: [String] = []
                while index < lines.count,
                      !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    code.append(lines[index])
                    index += 1
                }
                if index < lines.count { index += 1 }
                blocks.append(.code(lines: code, language: language))
                continue
            }

            if isRule(trimmed) {
                flushParagraph()
                blocks.append(.rule)
                index += 1
                continue
            }

            if let heading = matchHeading(trimmed) {
                flushParagraph()
                blocks.append(.heading(level: heading.0, text: heading.1))
                index += 1
                continue
            }

            // An image ALONE on its own line — see the file header for why
            // this is a block and not an inline run. Checked before
            // quote/table/list: none of those markers ("#", ">", "-", "*",
            // "+", a digit, "|") can appear first on a line starting "![",
            // so this can never shadow them.
            if let image = matchImageOnly(trimmed) {
                flushParagraph()
                blocks.append(.image(alt: image.alt, url: image.url))
                index += 1
                continue
            }

            if let first = matchQuote(trimmed) {
                flushParagraph()
                var quoted = [first]
                index += 1
                while index < lines.count,
                      let more = matchQuote(lines[index].trimmingCharacters(in: .whitespaces)) {
                    quoted.append(more)
                    index += 1
                }
                blocks.append(.quote(quoted))
                continue
            }

            // Tables, before the list/paragraph fallthrough. A line is only
            // a table when the line UNDER it is a separator row — prose that
            // merely contains a pipe must never be captured.
            if let table = matchTable(lines, index) {
                flushParagraph()
                blocks.append(.table(header: table.header, rows: table.rows))
                index = table.next
                continue
            }

            if let marker = matchListMarker(line) {
                flushParagraph()
                let ordered = marker.ordered
                var items: [MarkdownListItem] = []
                while index < lines.count {
                    guard let m = matchListMarker(lines[index]), m.ordered == ordered else { break }
                    items.append(MarkdownListItem(text: m.content, depth: m.indent / 2))
                    index += 1
                }
                blocks.append(.list(items: items, ordered: ordered))
                continue
            }

            paragraph.append(trimmed)
            index += 1
        }

        flushParagraph()
        return blocks
    }

    private static func isRule(_ line: String) -> Bool {
        guard line.count >= 3 else { return false }
        let set = Set(line)
        return set.count == 1 && (set.first == "-" || set.first == "*" || set.first == "_")
    }

    private static func matchHeading(_ line: String) -> (Int, String)? {
        var level = 0
        var rest = Substring(line)
        while let first = rest.first, first == "#", level < 6 {
            level += 1
            rest = rest.dropFirst()
        }
        guard level > 0, rest.first == " " else { return nil }
        return (level, String(rest).trimmingCharacters(in: .whitespaces))
    }

    /// `![alt](url)`, and the WHOLE trimmed line — nothing before or after
    /// it. Mirrors the web's own `IMAGE_RE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/`
    /// exactly: alt cannot contain `]`, url cannot contain `)` or
    /// whitespace, and the match must reach the end of the line. Does NOT
    /// apply the URL policy — that is `DocumentImageURLPolicy`'s job, run at
    /// render/load time so a refused image still keeps its alt text as
    /// data (parsing must never decide security).
    private static func matchImageOnly(_ line: String) -> (alt: String, url: String)? {
        guard line.hasPrefix("![") else { return nil }
        let afterBang = line.index(line.startIndex, offsetBy: 2)
        guard let closeBracket = line[afterBang...].firstIndex(of: "]") else { return nil }
        let alt = String(line[afterBang..<closeBracket])

        let afterBracket = line.index(after: closeBracket)
        guard afterBracket < line.endIndex, line[afterBracket] == "(" else { return nil }
        let urlStart = line.index(after: afterBracket)
        guard let closeParen = line[urlStart...].firstIndex(of: ")") else { return nil }
        let url = String(line[urlStart..<closeParen])
        guard !url.isEmpty, !url.contains(where: { $0.isWhitespace }) else { return nil }

        // The image must be the ENTIRE line — nothing trails the ")".
        guard line.index(after: closeParen) == line.endIndex else { return nil }
        return (alt, url)
    }

    private static func matchQuote(_ line: String) -> String? {
        guard line.hasPrefix(">") else { return nil }
        var rest = Substring(line).dropFirst()
        if rest.first == " " { rest = rest.dropFirst() }
        return String(rest)
    }

    private static func matchListMarker(_ line: String) -> (indent: Int, ordered: Bool, content: String)? {
        let indent = line.prefix { $0 == " " }.count
        let rest = line.dropFirst(indent)
        guard let first = rest.first else { return nil }

        if first == "-" || first == "*" || first == "+" {
            let after = rest.dropFirst()
            guard after.first == " " else { return nil }
            return (indent, false, String(after.dropFirst()).trimmingCharacters(in: .whitespaces))
        }

        let digits = rest.prefix { $0.isNumber }
        guard !digits.isEmpty else { return nil }
        let afterDigits = rest.dropFirst(digits.count)
        guard afterDigits.first == ".", afterDigits.dropFirst().first == " " else { return nil }
        return (indent, true, String(afterDigits.dropFirst(2)).trimmingCharacters(in: .whitespaces))
    }
}

struct MarkdownListItem: Equatable {
    let text: String
    let depth: Int
}


// MARK: - Table parsing

/// Splits `| a | b |` into `["a", "b"]`. Handles an escaped pipe (`\|`) and
/// a row written without the outer pipes, which is legal markdown.
func markdownSplitRow(_ line: String) -> [String] {
    var cells: [String] = []
    var current = ""
    var escaped = false
    for ch in line {
        if escaped { current.append(ch); escaped = false; continue }
        if ch == "\\" { escaped = true; continue }
        if ch == "|" { cells.append(current); current = "" } else { current.append(ch) }
    }
    cells.append(current)
    if let first = cells.first, first.trimmingCharacters(in: .whitespaces).isEmpty { cells.removeFirst() }
    if let last = cells.last, last.trimmingCharacters(in: .whitespaces).isEmpty { cells.removeLast() }
    return cells.map { $0.trimmingCharacters(in: .whitespaces) }
}

/// `| --- | :--: |` — the row that makes the line above it a header. This is
/// the ONLY thing that promotes a pipe-bearing line to a table, which is
/// what keeps a sentence containing "5 | 10" from being eaten.
func markdownIsSeparatorRow(_ line: String) -> Bool {
    let cells = markdownSplitRow(line)
    guard !cells.isEmpty else { return false }
    for cell in cells {
        let body = cell.replacingOccurrences(of: ":", with: "")
        if body.isEmpty { return false }
        if body.contains(where: { $0 != "-" }) { return false }
    }
    return true
}

struct MarkdownTableMatch {
    let header: [String]
    let rows: [[String]]
    let next: Int
}

func matchTable(_ lines: [String], _ start: Int) -> MarkdownTableMatch? {
    guard start + 1 < lines.count else { return nil }
    let head = lines[start].trimmingCharacters(in: .whitespaces)
    let separator = lines[start + 1].trimmingCharacters(in: .whitespaces)
    guard head.contains("|"), markdownIsSeparatorRow(separator) else { return nil }

    let header = markdownSplitRow(head)
    guard !header.isEmpty else { return nil }

    var index = start + 2
    var rows: [[String]] = []
    while index < lines.count {
        let trimmed = lines[index].trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty || !trimmed.contains("|") { break }
        rows.append(markdownSplitRow(trimmed))
        index += 1
    }
    return MarkdownTableMatch(header: header, rows: rows, next: index)
}

// MARK: - Image URL policy

/// Mirrors the web app's `safeMarkdownLiteImageSrc`
/// (`frontend/lib/workspace/markdown-lite.tsx`) EXACTLY: same allowed
/// schemes, same `.svg` refusal, same "clean control characters, trim, then
/// decide" order. A URL the web would refuse must never render as an image
/// here either — this policy is a SECURITY decision, not a feature gap, so
/// do not widen it.
///
/// Deliberately NARROWER than the web on one point: the web also allows a
/// same-origin relative path (`cleaned.startsWith("/")`), because a browser
/// resolves that against the page's own origin. This app has no document
/// base URL to resolve one against, so a relative path is refused rather
/// than guessed at — narrowing, never widening, of the same policy.
enum DocumentImageURLPolicy {
    private static let allowedSchemes: Set<String> = ["http", "https"]

    /// Strips characters browsers ignore when sniffing a scheme (tab,
    /// newline, carriage return — "java\tscript:" is a classic filter-bypass
    /// trick), then trims surrounding whitespace. Byte-for-byte the same
    /// operation as the web's own `cleanUrl`.
    static func clean(_ raw: String) -> String {
        var cleaned = raw
        cleaned.removeAll { $0 == "\t" || $0 == "\n" || $0 == "\r" }
        return cleaned.trimmingCharacters(in: .whitespaces)
    }

    /// True if `url`'s path — ignoring query and fragment — ends in `.svg`,
    /// case-insensitively. An SVG is a program, not just a picture; see
    /// CLAUDE.md's upload-policy note, which this mirrors: attachments are
    /// served straight back from the workspace's own origin, so this
    /// renderer must never give one a path to execute as an image.
    /// Structurally identical to the web's own `hasSvgExtension`: split on
    /// "#" first, then "?", and test only the remaining path.
    static func hasSvgExtension(_ url: String) -> Bool {
        let withoutFragment = url.split(separator: "#", maxSplits: 1, omittingEmptySubsequences: false).first
            .map(String.init) ?? url
        let path = withoutFragment.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false).first
            .map(String.init) ?? withoutFragment
        return path.lowercased().hasSuffix(".svg")
    }

    /// The URL to actually fetch, or nil if policy refuses it. Only an
    /// absolute `http:`/`https:` URL with a real host is ever returned —
    /// never `data:`, `javascript:`, a bare scheme-less string, or a `.svg`
    /// path of any scheme.
    static func resolvedImageURL(_ raw: String) -> URL? {
        let cleaned = clean(raw)
        guard !cleaned.isEmpty else { return nil }
        guard !hasSvgExtension(cleaned) else { return nil }
        guard let colonIndex = cleaned.firstIndex(of: ":") else { return nil }
        let scheme = cleaned[cleaned.startIndex..<colonIndex]
        guard let first = scheme.first, first.isLetter,
              scheme.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "+" || $0 == "." || $0 == "-" })
        else { return nil }
        guard allowedSchemes.contains(scheme.lowercased()) else { return nil }
        guard let url = URL(string: cleaned), let host = url.host, !host.isEmpty else { return nil }
        return url
    }
}

// MARK: - Image rendering

/// One document image: loading, loaded, or failed — the outcome-honesty law
/// applied to pictures. A silently blank space (the pre-existing defect —
/// `![alt](url)` fell through to plain text with the picture simply gone)
/// and a generically-blank "couldn't load" box are DIFFERENT lies; this view
/// draws a real state for each of the three, and a refused-by-policy image
/// (a `.svg`, or a non-http(s) scheme) is presented as its own distinct
/// reason rather than folded into a network failure.
///
/// A CUSTOM loader, not `AsyncImage`: this view also has to answer "how big
/// is this on screen" — a document image must shrink to fit the reading
/// column on the smallest device but never be stretched past its own
/// natural size to fill a wider one — and `AsyncImage`'s `.success` phase
/// hands back an opaque SwiftUI `Image` with no size to read. Fetching into
/// a `UIImage` first gives `.size` to constrain the frame against. Zero SPM
/// dependencies either way: `URLSession` + `UIImage(data:)`, both already
/// Foundation/UIKit.
struct DocumentImageView: View {
    let alt: String
    let url: String
    @Environment(\.colorScheme) private var scheme

    private enum Phase {
        case loading
        case loaded(UIImage)
        case failed(reason: String)
    }

    @State private var phase: Phase = .loading

    var body: some View {
        content
            // Keyed on the url: if the same rendered view is ever reused for
            // a different image (should not happen given block identity is
            // the array offset, but this is the honest guard), the fetch
            // restarts rather than showing the previous image's result.
            .task(id: url) { await load() }
    }

    @ViewBuilder
    private var content: some View {
        switch phase {
        case .loading:
            placeholder {
                HStack(spacing: Space.x2) {
                    ProgressView()
                        .tint(Theme.textMuted(scheme))
                    Text("Loading image…")
                        .font(.empSecondary)
                        .foregroundStyle(Theme.textMuted(scheme))
                }
            }

        case let .loaded(image):
            // `.resizable()` + `.aspectRatio(.fit)` lets the image SHRINK to
            // fit a narrower parent; capping `maxWidth`/`maxHeight` at the
            // image's own natural size is what stops it being STRETCHED UP
            // to fill a column wider than the picture itself. `image.size`
            // is already in points (UIImage factors in `.scale`), so this
            // is the same unit the parent's own layout works in.
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(maxWidth: image.size.width, maxHeight: image.size.height)
                .frame(maxWidth: .infinity, alignment: .leading)
                .clipShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                        .stroke(Theme.border(scheme), lineWidth: 1)
                )
                .accessibilityLabel(alt.isEmpty ? "Image" : alt)

        case let .failed(reason):
            placeholder {
                VStack(alignment: .leading, spacing: Space.x1) {
                    HStack(spacing: Space.x2) {
                        Image(systemName: "photo")
                            .foregroundStyle(Theme.textMuted(scheme))
                        Text(reason)
                            .font(.empSecondary)
                            .foregroundStyle(Theme.textSecondary(scheme))
                    }
                    // The alt text is what makes this a "something exists
                    // here" state rather than a bare error box — never
                    // rendered as nothing, per the outcome-honesty law.
                    if !alt.isEmpty {
                        Text(alt)
                            .font(.empSecondary)
                            .foregroundStyle(Theme.textMuted(scheme))
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func placeholder<Content: View>(@ViewBuilder _ inner: () -> Content) -> some View {
        HStack {
            inner()
            Spacer(minLength: 0)
        }
        .padding(Space.x3)
        .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
        .background(Theme.bgInset(scheme), in: RoundedRectangle(cornerRadius: Radius.card))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                .stroke(Theme.border(scheme), lineWidth: 1)
        )
    }

    /// Policy is checked FIRST, synchronously, before any network attempt —
    /// a refused URL never makes a request, matching the web's own
    /// sanitize-before-render order. A network/decode failure is reported
    /// with a distinct sentence from a policy refusal, because they are
    /// different facts: one is "this could not be shown to you", the other
    /// is "this could not be reached."
    private func load() async {
        guard let resolvedURL = DocumentImageURLPolicy.resolvedImageURL(url) else {
            phase = .failed(reason: "This image can't be shown")
            return
        }
        do {
            let (data, response) = try await URLSession.shared.data(from: resolvedURL)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                phase = .failed(reason: "Couldn't load this image")
                return
            }
            guard let uiImage = UIImage(data: data) else {
                phase = .failed(reason: "Couldn't load this image")
                return
            }
            phase = .loaded(uiImage)
        } catch {
            phase = .failed(reason: "Couldn't load this image")
        }
    }
}
