import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Radius, Space, Theme, Type } from '../theme';

/**
 * THE LIST ROW, per the design language read off Linear's real app:
 *
 *   ~56pt tall, NO DIVIDERS — spacing separates rows, not rules.
 *   unread  = a dot AND full-brightness text
 *   read    = no dot AND the whole row dims
 *   never a background change; the row's surface is the page.
 *
 * THE SUBTITLE IS THE REASON, then the age. The reason string is NOT
 * computed here — `inbox-needs-you.ts` already decided it ("Blocked",
 * "Needs your input", the notification's own body, a failed run's summary)
 * and computing a second one locally is how two surfaces start disagreeing
 * about the same row.
 *
 * The dot means UNATTENDED, which is one fact across three kinds: a
 * notification is unattended until it is read; a stuck task and a blocked
 * run are unattended for as long as they are still stuck or still failed —
 * they carry no read state of their own, and inventing one would be a
 * second, quieter place the row's status is decided.
 */
export function InboxRow({
  title,
  reason,
  age,
  unread,
  onPress,
}: {
  title: string;
  reason: string | null;
  age: string;
  unread: boolean;
  onPress?: () => void;
}) {
  const dim = !unread;
  const subtitle = reason ? `${reason} · ${age}` : age;

  const body = (
    <View style={styles.row}>
      <View style={styles.dotColumn}>
        {unread ? <View style={styles.dot} /> : null}
      </View>
      <View style={styles.text}>
        <Text
          style={[styles.title, dim && styles.titleDim]}
          numberOfLines={1}
        >
          {title}
        </Text>
        <Text
          style={[styles.subtitle, dim && styles.subtitleDim]}
          numberOfLines={1}
        >
          {subtitle}
        </Text>
      </View>
    </View>
  );

  // A row with nowhere to go is not pressable. Phase 1 has no task or agent
  // detail screen, so most rows render as plain text — the shared module
  // says so itself by handing back a null href, and wrapping them in a
  // Pressable that does nothing would be exactly the dead control that
  // contract exists to avoid.
  if (!onPress) return body;

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      style={({ pressed }) => pressed && styles.pressed}
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    minHeight: 56,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Space.x5,
    paddingVertical: Space.x2,
  },
  pressed: { backgroundColor: Theme.bgInset, borderRadius: Radius.row },
  dotColumn: { width: 18, alignItems: 'flex-start', justifyContent: 'center' },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: Theme.unread },
  text: { flex: 1, gap: 2 },
  title: { ...Type.rowTitle, color: Theme.textPrimary },
  titleDim: { color: Theme.textDimmed },
  subtitle: { ...Type.rowSubtitle, color: Theme.textMuted },
  subtitleDim: { color: Theme.textDimmed },
});
