import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Radius, Space, Theme, Type } from '../theme';

/**
 * "EMPTY" AND "COULDN'T LOAD" ARE DIFFERENT FACTS AND NEVER SHARE A SCREEN.
 * That is a standing product law here, not a nicety: the two want opposite
 * responses — one says you are done, the other says try again — and a
 * surface that collapses them tells someone their inbox is clear when the
 * request failed.
 *
 * There are three states, and a fourth that is deliberately not one:
 *   Skeleton  nothing is known yet
 *   Failure   we asked and could not find out              (offers Retry)
 *   Empty     we asked, found out, and the answer is none  (no Retry —
 *             retrying a correct answer is a dead control)
 *   ...and a refresh that fails while content is already on screen is NOT
 *   rendered here at all; the list keeps showing what is true and the
 *   failure is reported beside it.
 */

export function SkeletonList({ rows = 5 }: { rows?: number }) {
  return (
    <View accessibilityLabel="Loading" style={styles.skeletonWrap}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={styles.skeletonRow}>
          <View style={styles.skeletonDot} />
          <View style={styles.skeletonText}>
            <View style={[styles.skeletonBar, { width: `${62 + ((i * 11) % 26)}%` }]} />
            <View style={[styles.skeletonBar, styles.skeletonBarShort, { width: `${34 + ((i * 7) % 18)}%` }]} />
          </View>
        </View>
      ))}
    </View>
  );
}

export function SurfaceFailure({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <View style={styles.center}>
      <Ionicons name="cloud-offline-outline" size={26} color={Theme.textMuted} />
      <Text style={styles.centerTitle}>{title}</Text>
      <Text style={styles.centerBody}>{message}</Text>
      <Pressable
        accessibilityRole="button"
        onPress={onRetry}
        style={({ pressed }) => [styles.retry, pressed && styles.retryPressed]}
      >
        <Text style={styles.retryLabel}>Try again</Text>
      </Pressable>
    </View>
  );
}

export function SurfaceEmpty({ title, body }: { title: string; body: string }) {
  return (
    <View style={styles.center}>
      <Ionicons name="checkmark-done-outline" size={26} color={Theme.textMuted} />
      <Text style={styles.centerTitle}>{title}</Text>
      <Text style={styles.centerBody}>{body}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  skeletonWrap: { paddingTop: Space.x2 },
  skeletonRow: {
    minHeight: 56,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Space.x5,
    paddingVertical: Space.x2,
  },
  skeletonDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: Theme.bgLift, marginRight: 10 },
  skeletonText: { flex: 1, gap: 6 },
  // bgCard, not bgInset: over a pure-black page an inset trough composites
  // dark enough to disappear, which is how a "loading" state reads as an
  // empty one.
  skeletonBar: { height: 11, borderRadius: 5, backgroundColor: Theme.bgCard },
  skeletonBarShort: { height: 9, backgroundColor: Theme.bgInset },

  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: Space.x2,
    paddingHorizontal: Space.x8,
    paddingBottom: Space.x8,
  },
  centerTitle: { ...Type.rowTitle, color: Theme.textSecondary, marginTop: Space.x1 },
  centerBody: { ...Type.rowSubtitle, color: Theme.textMuted, textAlign: 'center', lineHeight: 19 },
  retry: {
    marginTop: Space.x3,
    paddingHorizontal: Space.x5,
    paddingVertical: Space.x2 + 2,
    borderRadius: Radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: Theme.borderStrong,
    backgroundColor: Theme.bgCard,
  },
  retryPressed: { backgroundColor: Theme.bgLift },
  retryLabel: { ...Type.button, color: Theme.textPrimary, fontSize: 14 },
});
