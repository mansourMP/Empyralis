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

/** The skeleton mirrors InboxRow's real geometry — 40pt well, 13pt gutter,
 *  69pt pitch. A loading state built to different measurements makes the
 *  list visibly jump the moment content arrives, which reads as a glitch
 *  rather than as loading. */
export function SkeletonList({ rows = 6 }: { rows?: number }) {
  return (
    <View accessibilityLabel="Loading" style={styles.skeletonWrap}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={styles.skeletonRow}>
          <View style={styles.skeletonWell} />
          <View style={styles.skeletonText}>
            <View style={[styles.skeletonBar, { width: `${62 + ((i * 11) % 26)}%` }]} />
            <View
              style={[
                styles.skeletonBar,
                styles.skeletonBarShort,
                { width: `${34 + ((i * 7) % 18)}%` },
              ]}
            />
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
  skeletonWrap: { paddingTop: Space.x1 },
  skeletonRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Space.x5,
    paddingVertical: 14,
  },
  skeletonWell: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: Theme.bgCard,
    marginRight: 13,
  },
  skeletonText: { flex: 1, gap: 7 },
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
  centerBody: { ...Type.rowSubtitle, color: Theme.textMuted, textAlign: 'center', lineHeight: 21 },
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
