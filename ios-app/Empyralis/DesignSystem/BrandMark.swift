import SwiftUI

/// The Empyralis mark, drawn natively rather than shipped as a bitmap.
///
/// A DIRECT PORT of frontend/public/brand-assets/empyralis/empyralis-mark.svg
/// — four shapes in a 512×512 viewBox, same geometry, same hex values. Drawn
/// rather than rasterized because this renders at 160pt on the welcome
/// screen: the largest PNG in the repo is 180px, which would be visibly soft
/// at @3x (480px of pixels needed), and the mark is simple enough that
/// vector fidelity is free.
///
/// THE MARK IS CLAY/ORANGE AND THE UI ACCENT IS VIOLET. That is deliberate
/// and is the one documented exception in the web app's own token file —
/// do not "fix" the mark to match the accent, and do not paint UI in these
/// three hexes.
///
/// If the SVG ever changes, change this with it. The founder's mark is not
/// edited here; this only mirrors it.
struct BrandMark: View {
    var size: CGFloat = 64

    // Straight from the SVG. A unit is 1/512th of the rendered size.
    private static let viewBox: CGFloat = 512

    var body: some View {
        Canvas { context, canvasSize in
            let scale = min(canvasSize.width, canvasSize.height) / Self.viewBox

            func bar(x: CGFloat, y: CGFloat, width: CGFloat, height: CGFloat, hex: UInt32) {
                let rect = CGRect(
                    x: x * scale,
                    y: y * scale,
                    width: width * scale,
                    height: height * scale
                )
                // rx=28 on a 56-tall bar is a full capsule end.
                let path = Path(roundedRect: rect, cornerRadius: 28 * scale)
                context.fill(path, with: .color(Color(hex: hex)))
            }

            bar(x: 128, y: 136, width: 256, height: 56, hex: 0xF2A65A)
            bar(x: 128, y: 228, width: 168, height: 56, hex: 0xE8853D)

            let dotRadius: CGFloat = 30 * scale
            let dot = Path(
                ellipseIn: CGRect(
                    x: 352 * scale - dotRadius,
                    y: 256 * scale - dotRadius,
                    width: dotRadius * 2,
                    height: dotRadius * 2
                )
            )
            context.fill(dot, with: .color(Color(hex: 0xE8853D)))

            bar(x: 128, y: 320, width: 256, height: 56, hex: 0xC95F27)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// The mark on a rounded-square tile, the way an app icon reads. Used as the
/// welcome screen's hero so the first thing someone sees matches the icon
/// they just tapped on the home screen.
struct BrandMarkTile: View {
    var size: CGFloat = 160
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.2237, style: .continuous)
                .fill(scheme == .dark ? Color(hex: 0x161616) : Color(hex: 0xF7F7F8))
                .overlay(
                    RoundedRectangle(cornerRadius: size * 0.2237, style: .continuous)
                        .stroke(Theme.border(scheme), lineWidth: 1)
                )
            BrandMark(size: size * 0.72)
        }
        .frame(width: size, height: size)
        // iOS icon corner radius is ~22.37% of the side — the "squircle"
        // ratio. .continuous is what makes it a squircle rather than a
        // plain rounded rect; the difference is visible at this size.
        .accessibilityHidden(true)
    }
}
