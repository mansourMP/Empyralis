import { StyleSheet, Text, View } from 'react-native';

import { ScreenHeader } from '../components/ScreenHeader';
import { Space, Theme, Type } from '../theme';

/**
 * Phase 1 builds ONE screen end to end. The other three tabs exist so the
 * tab bar's shape can be judged, and they say plainly that they are not
 * built rather than showing a fake list or an empty state that would read
 * as "you have no projects".
 *
 * "Not built yet" and "you have none" are different facts — the same law
 * that keeps empty and couldn't-load apart, pointed one step earlier.
 */
export function StubScreen({ title }: { title: string }) {
  return (
    <View style={styles.screen}>
      <ScreenHeader title={title} actions={[]} />
      <View style={styles.center}>
        <Text style={styles.title}>Not built yet</Text>
        <Text style={styles.body}>
          Phase 1 proves the Inbox end to end. {title} comes next.
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Theme.bgPage },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: Space.x2,
    paddingHorizontal: Space.x8,
    paddingBottom: Space.x8,
  },
  title: { ...Type.rowTitle, color: Theme.textSecondary },
  body: { ...Type.rowSubtitle, color: Theme.textMuted, textAlign: 'center', lineHeight: 19 },
});
