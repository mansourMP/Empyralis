import SwiftUI
import UIKit

/// The single source of colour truth. LIGHT mode is ported verbatim from the
/// web app's lib/ui/theme-tokens.css so the two products read as ONE product.
/// When a light token changes there, change it here — never invent an
/// iOS-only colour.
///
/// THE ACCENT LAW CARRIES OVER FROM THE WEB APP AND IS NOT NEGOTIABLE HERE:
/// violet appears ONLY on the single primary-action button in a view.
/// Not on selected rows, not on tab bars, not on badges, not on toggles,
/// not on links. Selection is weight and shape, never hue. Semantic status
/// (online/working/blocked) is its own vocabulary and is never "the accent".
///
/// ═══════════════════════════════════════════════════════════════════════
/// THE PHONE'S DARK RAMP DELIBERATELY DIVERGES FROM THE WEB. DO NOT "FIX"
/// IT BACK TO PARITY.
/// ═══════════════════════════════════════════════════════════════════════
///
/// Founder's instruction, 2026-08-25: *"I want pure black in my phone
/// application, not like what I have on the platform."* The web's dark
/// surfaces (`#1A1A1A` page, `#292929` card) are DESKTOP values, authored
/// for an LCD panel where a true black is unreachable anyway and a raised
/// grey is the only way to say "page". On an OLED phone those same values
/// keep every pixel lit and read as washed-out grey beside the system's own
/// black chrome. `bgPage` is `#000000` here and the pixels are genuinely
/// off.
///
/// A one-value change would have made cards invisible, so the WHOLE dark
/// ramp is re-derived rather than patched. The derivation is not taste — for
/// each surface the target is *the same perceptual separation from its own
/// page* that the web token has from its own page, recomputed against black:
///
/// ```
/// token      web dark   phone dark   contrast vs. its page   web's own ratio
/// bgPage     #1A1A1A ->  #000000     —                       —
/// bgInset    #1D1D1D ->  #161616     1.16 : 1                1.16  (light)
/// bgRail     #232323 ->  #191919     1.19 : 1                between the two
/// bgCard     #292929 ->  #1C1C1C     1.23 : 1                1.20  (dark)
/// border     w/0.08  ->  w/0.14      1.35 : 1                1.25
/// borderStrong w/0.16 -> w/0.24      1.94 : 1                1.64
/// ```
///
/// Three things that only fall out of doing the arithmetic:
///
/// 1. **`bgInset` INVERTS DIRECTION.** On the web "inset" means carved
///    *below* the page (`#EEEEEF` under a white page). There is nothing
///    below black, so on the phone the recessed surface must become a faint
///    RAISE. It is still the quietest filled block in the app — it just
///    reaches that role from the other side.
/// 2. **A hairline loses ~10% of its contrast on black and must be paid
///    back.** `white.opacity(0.08)` composites to `#2C2C2C` over `#1A1A1A`
///    (1.25:1) but only to `#141414` over `#000000` (1.14:1) — the same
///    token, visibly fainter, which is exactly how borders vanish on OLED.
///    The alphas are raised so the *composited* hairline lands above where
///    the web's does, not at the same alpha.
/// 3. **Nothing in the text ramp needed re-deriving.** Every text token gets
///    strictly MORE contrast on black (`textMuted` goes 4.4:1 -> 7.5:1,
///    `textSecondary` -> 13.4:1, `textPrimary` -> 19.1:1), and `textPrimary`
///    staying `#F4F4F5` rather than pure white is what keeps OLED halation
///    off the glyph edges. The accent and every status hue likewise only
///    gain contrast, so the accent law is untouched.
///
/// **Light mode is byte-for-byte unchanged, and the web platform's own
/// tokens are NOT touched by any of this.** This divergence is scoped to
/// one column of one file.
enum Theme {

    // MARK: - Surfaces

    /// TRUE BLACK on the phone — see the divergence note above.
    static func bgPage(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x000000) : Color(hex: 0xFFFFFF)
    }

    /// The raised content surface. `#1C1C1C` is also where iOS puts its own
    /// secondary background on a black page, so a card sits at the level the
    /// platform's sheets and search fields already occupy — nothing on
    /// screen looks like it belongs to a different app.
    static func bgCard(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x1C1C1C) : Color(hex: 0xFFFFFF)
    }

    /// The quietest filled block (skeletons excepted): code blocks, chips,
    /// a disabled control's fill. On black this is a faint RAISE rather than
    /// the web's recess — same role, opposite direction.
    static func bgInset(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x161616) : Color(hex: 0xEEEEEF)
    }

    static func bgRail(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x191919) : Color(hex: 0xF4F4F5)
    }

    /// A text INPUT's fill. Named rather than repeated as a literal because
    /// it cannot simply be `bgCard`: in light mode a card is white and a
    /// field on a white page would be invisible but for its hairline. Two
    /// call sites used to carry this pair as magic numbers.
    static func bgField(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x1C1C1C) : Color(hex: 0xF2F2F4)
    }

    // MARK: - Text

    static func textPrimary(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0xF4F4F5) : Color(hex: 0x18181B)
    }

    static func textSecondary(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0xCECECE) : Color(hex: 0x52525B)
    }

    static func textMuted(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x9A9A9A) : Color(hex: 0x6B6B76)
    }

    // MARK: - Borders

    /// 0.14, not the web's 0.08 — an alpha hairline is composited against
    /// whatever it lands on, so the SAME token is visibly fainter over black
    /// (1.14:1) than over `#1A1A1A` (1.25:1). The alpha is raised so the
    /// rendered line lands above where the web's does, which is the whole
    /// reason borders survive on OLED.
    static func border(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color.white.opacity(0.14) : Color.black.opacity(0.08)
    }

    static func borderStrong(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color.white.opacity(0.24) : Color.black.opacity(0.16)
    }

    // MARK: - Accent (primary action ONLY — see the law above)

    static func accent(_ scheme: ColorScheme) -> Color {
        // oklch(64% 0.17 305) dark / oklch(57% 0.155 305) light, converted.
        scheme == .dark ? Color(hex: 0xA56DDE) : Color(hex: 0x8D5BBF)
    }

    static let accentContrast = Color.white

    // MARK: - Semantic status (a separate vocabulary — never the accent)

    static func online(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0x22C55E) : Color(hex: 0x15803D)
    }

    static func offline(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0xF87171) : Color(hex: 0xB91C1C)
    }

    static func warning(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(hex: 0xFBBF24) : Color(hex: 0xB45309)
    }

    // MARK: - Task status (mirrors --task-* exactly)

    static func taskStatus(_ status: String, _ scheme: ColorScheme) -> Color {
        switch status {
        case "in_progress":
            return scheme == .dark ? Color(hex: 0xF2B23E) : Color(hex: 0xD97706)
        case "awaiting_input":
            return scheme == .dark ? Color(hex: 0x5E9CF5) : Color(hex: 0x2563EB)
        case "blocked":
            return Color(hex: 0xEF4444)
        case "in_review":
            return Color(hex: 0x4CB782)
        case "done":
            return scheme == .dark ? Color(hex: 0x2F9E68) : Color(hex: 0x0F6B34)
        default:
            return scheme == .dark ? Color(hex: 0x8A8F98) : Color(hex: 0x6B7280)
        }
    }
}

// MARK: - Type scale (mirrors --text-* — 14pt body, an operator-tool density)

/// `Font.system(size:weight:design:)` — the initializer every token below
/// used until this pass — is FIXED: Apple's own documentation for it states
/// it does not adjust for Dynamic Type, unlike the semantic style-based
/// overload (`Font.system(.body)`). Verified live, not just read: an
/// accessibility-size screenshot of the pre-fix build was PIXEL-IDENTICAL
/// to the default-size one on every screen — the whole app was ignoring the
/// reader's text-size setting completely, not "clipping at large sizes" but
/// never growing past the default at all.
///
/// `UIFontMetrics(forTextStyle:).scaledValue(for:)` is Apple's documented
/// mechanism for a CUSTOM base size that still rides one of the built-in
/// scaling curves — `relativeTo` only picks the curve, the base size stays
/// exactly what each token below already specified, so nothing renders
/// differently at the system default size than it did before this fix.
///
/// `static var`, not `static let`: a `let` would compute the scaled value
/// ONCE per process (correct for this pass's own test, which sets the size
/// category as a launch argument, but wrong for a real reader who changes
/// Settings while the app stays open) and never update again. A computed
/// property re-reads the current trait environment on every access, which
/// is what lets it follow a live change the same way the rest of iOS does.
extension Font {
    static func empScaled(
        _ size: CGFloat,
        weight: Weight,
        design: Design = .default,
        relativeTo style: TextStyle = .body
    ) -> Font {
        let scaled = UIFontMetrics(forTextStyle: style.uiKitTextStyle).scaledValue(for: size)
        return .system(size: scaled, weight: weight, design: design)
    }

    static var empBody: Font { empScaled(15, weight: .regular, relativeTo: .body) }
    static var empBodyMedium: Font { empScaled(15, weight: .medium, relativeTo: .body) }
    static var empSecondary: Font { empScaled(13, weight: .regular, relativeTo: .subheadline) }
    static var empCaption: Font { empScaled(12, weight: .regular, relativeTo: .caption) }
    static var empCaptionMedium: Font { empScaled(12, weight: .medium, relativeTo: .caption) }
    static var empMono: Font { empScaled(12, weight: .medium, design: .monospaced, relativeTo: .caption) }
    static var empTitle: Font { empScaled(24, weight: .semibold, relativeTo: .title) }
    static var empSectionHeader: Font { empScaled(12, weight: .semibold, relativeTo: .caption2) }
}

private extension Font.TextStyle {
    /// `UIFontMetrics` speaks `UIFont.TextStyle`, not SwiftUI's own
    /// `Font.TextStyle` — the two enums are not interchangeable despite the
    /// identical case names.
    var uiKitTextStyle: UIFont.TextStyle {
        switch self {
        case .largeTitle: return .largeTitle
        case .title: return .title1
        case .title2: return .title2
        case .title3: return .title3
        case .headline: return .headline
        case .body: return .body
        case .callout: return .callout
        case .subheadline: return .subheadline
        case .footnote: return .footnote
        case .caption: return .caption1
        case .caption2: return .caption2
        @unknown default: return .body
        }
    }
}

// MARK: - Spacing (4pt grid, mirrors --space-*)

enum Space {
    static let x1: CGFloat = 4
    static let x2: CGFloat = 8
    static let x3: CGFloat = 12
    static let x4: CGFloat = 16
    static let x5: CGFloat = 20
    static let x6: CGFloat = 24
    static let x8: CGFloat = 32
    static let x10: CGFloat = 40
}

// MARK: - Radii (tight — 6/8, never 12/14 generic-SaaS soft)

enum Radius {
    static let control: CGFloat = 6
    static let card: CGFloat = 8
    static let pill: CGFloat = 999
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}
