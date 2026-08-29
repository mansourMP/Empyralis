import { StyleSheet, View } from 'react-native';

/**
 * The Empyralis mark, DRAWN rather than shipped as an asset — three rounded
 * bars and a dot, ported unit-for-unit from
 * frontend/public/brand-assets/empyralis/empyralis-mark-compact.svg's own
 * 32-unit viewBox, same hex values.
 *
 * Drawn because the only raster the repo carries is 128px and the welcome
 * screen renders this large; the same call the iOS app made, for the same
 * reason.
 *
 * THE MARK IS CLAY AND THE UI ACCENT IS VIOLET. That split is deliberate and
 * already documented — do not "reconcile" them by tinting the mark.
 */
const UNITS = 32;

export function BrandMark({ size = 84 }: { size?: number }) {
  const u = size / UNITS;
  return (
    <View style={{ width: size, height: size }} accessibilityLabel="Empyralis">
      <View
        style={[
          styles.bar,
          { left: 1 * u, top: 2 * u, width: 30 * u, height: 6 * u, borderRadius: 3 * u, backgroundColor: '#F2A65A' },
        ]}
      />
      <View
        style={[
          styles.bar,
          { left: 1 * u, top: 13 * u, width: 18 * u, height: 6 * u, borderRadius: 3 * u, backgroundColor: '#E8853D' },
        ]}
      />
      <View
        style={[
          styles.bar,
          {
            left: 23 * u,
            top: 12 * u,
            width: 8 * u,
            height: 8 * u,
            borderRadius: 4 * u,
            backgroundColor: '#E8853D',
          },
        ]}
      />
      <View
        style={[
          styles.bar,
          { left: 1 * u, top: 24 * u, width: 30 * u, height: 6 * u, borderRadius: 3 * u, backgroundColor: '#C95F27' },
        ]}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { position: 'absolute' },
});
